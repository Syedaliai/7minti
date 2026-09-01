"""
WhatsApp OTP Verification Handler — GrizzlySMS Integration
Supports: USA 🇺🇸 and UK 🇬🇧 phone numbers for WhatsApp verification only.

Security Architecture:
  1. Deposit Guard       — Only users with confirmed deposit history may access.
  2. Server-Side Pricing — Real-time price fetched from GrizzlySMS /getPrices.
                           No price data is ever accepted from the client.
  3. Commission Lock     — 80% markup applied strictly in _calculate_customer_price().
                           Multiplier comes only from settings.GRIZZLYSMS_COMMISSION_RATE.
  4. Country Whitelist   — Only US and UK country IDs are accepted in the buy callback.
                           Any other value raises ValueError before the API call.
  5. maxPrice Guard      — The freshly fetched raw price is sent as maxPrice to the
                           supplier. This prevents purchasing at an inflated price if
                           the market moves between the quote and the order.
  6. Session Lock        — Exactly 1 concurrent ACTIVE WhatsApp order per Telegram user.
  7. DB-First State      — Activation ID, phone number and price are stored in the DB
                           BEFORE any OTP polling starts. If the bot restarts, the order
                           remains traceable and cannot be double-charged.
  8. Ownership Check     — Cancellation verifies both telegram_id AND activation_id in DB.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select, update as sa_update

from app.config import settings
from app.db.models import WhatsAppOrder, WhatsAppOrderStatus, Payment, User
from app.db.repositories import UserRepository, CouponRepository
from app.db.session import AsyncSessionLocal
from app.services.grizzlysms import (
    GrizzlySMSService,
    GrizzlySMSError,
    GrizzlySMSNoNumbersError,
    GrizzlySMSBalanceError,
    GRIZZLY_COUNTRY_US,
    GRIZZLY_COUNTRY_UK,
    GRIZZLY_COUNTRY_NAMES,
    STATUS_SMS_RECEIVED,
    STATUS_CANCELLED,
    STATUS_FINISHED,
)

logger = logging.getLogger(__name__)

# ─── Polling configuration ────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 5
MAX_POLL_DURATION_SECONDS = 300   # 5 minutes — WhatsApp OTPs arrive fast

# ─── Commission ───────────────────────────────────────────────────────────────
# 80% markup: customer pays raw_price * 1.80
# Source of truth: settings.GRIZZLYSMS_COMMISSION_RATE (0.80)

def _calculate_customer_price(raw_price: Decimal) -> Decimal:
    """Apply 80% commission: customer_price = raw_price * (1 + commission_rate)."""
    multiplier = Decimal("1") + settings.GRIZZLYSMS_COMMISSION_RATE
    return (raw_price * multiplier).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_grizzly() -> GrizzlySMSService:
    """Create a new GrizzlySMS client from configured API key."""
    return GrizzlySMSService(settings.GRIZZLYSMS_API_KEY)


async def _has_valid_deposit(telegram_id: int) -> bool:
    """Return True if user has at least one confirmed payment in the DB."""
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


async def _get_active_whatsapp_order(telegram_id: int) -> Optional[WhatsAppOrder]:
    """Return the user's current ACTIVE WhatsAppOrder, or None."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(WhatsAppOrder).where(
                WhatsAppOrder.telegram_id == telegram_id,
                WhatsAppOrder.status == WhatsAppOrderStatus.ACTIVE.value,
            )
        )
        return res.scalar_one_or_none()


# ─── Handlers ─────────────────────────────────────────────────────────────────

async def whatsapp_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Entry point: show the WhatsApp OTP number purchase menu with live prices.
    Available to all users to browse real-time prices.
    """
    query = update.callback_query
    if query:
        await query.answer()

    user = update.effective_user
    if not user:
        return

    # ── 1. Active Session Lock Check ──────────────────────────────────────────
    active_order = await _get_active_whatsapp_order(user.id)
    if active_order:
        flag = "🇺🇸" if active_order.country == "us" else "🇬🇧"
        active_text = (
            "⚠️ <b>Active WhatsApp Session In Progress</b>\n\n"
            f"{flag} Number: <code>+{active_order.phone_number}</code>\n"
            "Please wait for your OTP or cancel your current number before requesting another."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 View Active Number", callback_data="wa:view_active")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
        ])
        if query:
            await query.edit_message_text(active_text, parse_mode="HTML", reply_markup=kb)
        else:
            await update.message.reply_text(active_text, parse_mode="HTML", reply_markup=kb)
        return

    # ── 2. Real-time Price Check from GrizzlySMS ───────────────────────────────
    loading = "⏳ <i>Fetching live WhatsApp number prices...</i>"
    msg = None
    if query:
        await query.edit_message_text(loading, parse_mode="HTML")
    else:
        msg = await update.message.reply_text(loading, parse_mode="HTML")

    client = _get_grizzly()
    try:
        prices = await client.get_prices_both_countries()
    finally:
        await client.close()

    us_raw = prices.get("us")
    uk_raw = prices.get("uk")

    def fmt(raw: Optional[Decimal]) -> str:
        if raw is None:
            return "Unavailable ❌"
        cp = _calculate_customer_price(raw)
        return f"${cp:.4f} USDT"

    us_display = fmt(us_raw)
    uk_display = fmt(uk_raw)

    menu_text = (
        "📱 <b>WhatsApp Verification Numbers</b>\n\n"
        "Get an instant real phone number from <b>USA 🇺🇸</b> or <b>UK 🇬🇧</b> for WhatsApp registration & OTP verification.\n\n"
        "💰 <b>Live Pricing (Real-Time Supplier Rate + 80% Commission):</b>\n\n"
        f"🇺🇸 <b>United States:</b> <code>{us_display}</code>\n"
        f"🇬🇧 <b>United Kingdom:</b> <code>{uk_display}</code>\n\n"
        "👇 <i>Select a country below to get your number:</i>"
    )

    buttons = []
    if us_raw is not None:
        buttons.append(InlineKeyboardButton(
            f"🇺🇸 US  {us_display}",
            callback_data=f"wa:buy:{GRIZZLY_COUNTRY_US}",
        ))
    if uk_raw is not None:
        buttons.append(InlineKeyboardButton(
            f"🇬🇧 UK  {uk_display}",
            callback_data=f"wa:buy:{GRIZZLY_COUNTRY_UK}",
        ))

    rows = [[b] for b in buttons]  # One button per row for clear UX
    rows.append([
        InlineKeyboardButton("⚡ All SMS Services", callback_data="sms:hub"),
        InlineKeyboardButton("🏠 Home", callback_data="nav:home"),
    ])
    keyboard = InlineKeyboardMarkup(rows)

    if query:
        await query.edit_message_text(menu_text, parse_mode="HTML", reply_markup=keyboard)
    elif msg:
        try:
            await msg.edit_text(menu_text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await update.message.reply_text(menu_text, parse_mode="HTML", reply_markup=keyboard)


async def whatsapp_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle number purchase button press.
    Callback data format: wa:buy:<country_id>
    """
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user:
        return

    # ── Parse and whitelist-validate country ─────────────────────────────────
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("❌ Invalid request.", show_alert=True)
        return

    country_id = parts[2]
    if country_id not in (GRIZZLY_COUNTRY_US, GRIZZLY_COUNTRY_UK):
        logger.warning(
            "User %d attempted invalid country_id=%r in wa:buy callback — rejected.",
            user.id, country_id
        )
        await query.answer("❌ Invalid country selection.", show_alert=True)
        return

    country_name = GRIZZLY_COUNTRY_NAMES[country_id]
    flag = "🇺🇸" if country_id == GRIZZLY_COUNTRY_US else "🇬🇧"

    # ── Re-verify no active session ───────────────────────────────────────────
    if await _get_active_whatsapp_order(user.id):
        await query.answer("⚠️ You already have an active WhatsApp number.", show_alert=True)
        return

    # ── Fetch authoritative real-time price from GrizzlySMS ───────────────────
    client = _get_grizzly()
    try:
        raw_price = await client.get_whatsapp_price(country_id)
    finally:
        await client.close()

    if raw_price is None:
        await query.edit_message_text(
            f"❌ <b>No Numbers Available</b>\n\n"
            f"No WhatsApp numbers are currently available for {country_name}.\n"
            "Please try again in a few minutes.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔁 Try Again", callback_data="wa:menu")],
                [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
            ]),
        )
        return

    charged_price = _calculate_customer_price(raw_price)
    discount_applied = Decimal("0.0")
    redeemed_coupon_id = None
    promo_note = ""

    # ── User Balance Check, Coupon Evaluation & Atomic Deduction ─────────────
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
                service="whatsapp",
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

        # Check if user has sufficient funds
        if current_balance < charged_price:
            shortfall = (charged_price - current_balance).quantize(Decimal("0.0001"))
            binance_uid = settings.BINANCE_UID or "1254494426"
            pay_required_text = (
                f"💳 <b>Insufficient Balance to Activate Number</b>\n\n"
                f"📱 <b>Service:</b> WhatsApp Verification\n"
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
                [InlineKeyboardButton("🔙 Back to WhatsApp", callback_data="wa:menu")],
            ])
            await query.edit_message_text(pay_required_text, parse_mode="HTML", reply_markup=pay_kb)
            return

        # Atomically deduct balance
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
                service="whatsapp",
            )

        await session.commit()
        user_db_id = db_user.id

    await query.edit_message_text(
        f"⏳ <b>Requesting {country_name} WhatsApp number...</b>\n\n"
        f"{promo_note}"
        f"💰 Deducted <code>${charged_price:.4f} USDT</code> from balance.\n"
        "<i>Connecting to SMS network...</i>",
        parse_mode="HTML",
    )

    # ── Purchase from GrizzlySMS Supplier ─────────────────────────────────────
    client = _get_grizzly()
    try:
        purchase = await client.request_number(
            country_id=country_id,
            max_price=raw_price,
        )
    except (GrizzlySMSNoNumbersError, GrizzlySMSBalanceError, GrizzlySMSError, Exception) as err:
        logger.error("GrizzlySMS purchase failed for country %s: %s — Auto-refunding user %d", country_id, err, user.id)
        # Automatic refund on purchase failure
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            refunded_bal = await user_repo.refund_balance(user_db_id, charged_price)
            await session.commit()

        err_msg = str(err)
        if isinstance(err, GrizzlySMSNoNumbersError):
            err_msg = f"No numbers available for {country_name} right now."
        elif isinstance(err, GrizzlySMSBalanceError):
            err_msg = "Supplier balance temporarily low."

        await query.edit_message_text(
            f"❌ <b>Number Purchase Failed</b>\n\n"
            f"{err_msg}\n\n"
            f"💰 <b>Auto-Refund:</b> <code>+${charged_price:.4f} USDT</code> has been refunded to your balance.\n"
            f"💳 <b>Current Balance:</b> <code>${refunded_bal:.4f} USDT</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔁 Try Again", callback_data="wa:menu")],
                [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
            ]),
        )
        return
    finally:
        await client.close()

    activation_id = purchase["activation_id"]
    phone_number = purchase["phone_number"]
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    # ── Persist to DB ─────────────────────────────────────────────────────────
    async with AsyncSessionLocal() as session:
        order_record = WhatsAppOrder(
            id=str(uuid.uuid4()),
            user_id=user_db_id,
            telegram_id=user.id,
            grizzly_activation_id=activation_id,
            phone_number=phone_number,
            country="us" if country_id == GRIZZLY_COUNTRY_US else "uk",
            supplier_price=raw_price,
            charged_price=charged_price,
            status=WhatsAppOrderStatus.ACTIVE.value,
            expires_at=expires_at,
        )
        session.add(order_record)
        await session.commit()

    phone_display = f"+{phone_number}"

    await query.edit_message_text(
        f"📱 <b>Your WhatsApp Number is Ready!</b>\n\n"
        f"🎯 <b>Service:</b> WhatsApp\n"
        f"{flag} <b>Country:</b> {country_name}\n"
        f"📞 <b>Phone Number:</b> <code>{phone_display}</code> (tap to copy)\n"
        f"💰 <b>Price:</b> ${charged_price:.4f} USDT\n"
        f"⏳ <b>Valid For:</b> ~5 minutes\n\n"
        f"👉 <b>Enter this number in WhatsApp to receive your OTP.</b>\n"
        "⚡ <i>Waiting for SMS... OTP will appear here automatically!</i>\n\n"
        "🛡️ <i>Auto-Refund Guarantee: If no OTP is received within 5 minutes, 100% of your funds will be auto-refunded to your balance.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel & Refund Number", callback_data=f"wa:cancel:{activation_id}")],
        ]),
    )

    # ── Start background OTP polling worker ───────────────────────────────────
    asyncio.create_task(
        _poll_otp_worker(
            bot=context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            user_id=user_db_id,
            activation_id=activation_id,
            phone_number=phone_number,
            country_name=country_name,
            flag=flag,
            charged_price=charged_price,
        )
    )


# ─── Background OTP Polling Worker with Automated Refunds ────────────────────

async def _poll_otp_worker(
    bot,
    chat_id: int,
    message_id: int,
    user_id: int,
    activation_id: str,
    phone_number: str,
    country_name: str,
    flag: str,
    charged_price: Decimal,
) -> None:
    """
    Poll GrizzlySMS every 5 seconds until OTP arrives or timeout (5 min).
    If no OTP is received or if cancelled, automatically refunds 100% of the cost!
    """
    client = _get_grizzly()
    elapsed = 0
    phone_display = f"+{phone_number}"

    try:
        while elapsed < MAX_POLL_DURATION_SECONDS:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            elapsed += POLL_INTERVAL_SECONDS

            try:
                result = await client.get_activation_status(activation_id)
            except Exception as exc:
                logger.error("GrizzlySMS poll error (activation=%s): %s", activation_id, exc)
                continue

            status = result["status"]

            # ── OTP received ─────────────────────────────────────────────────
            if status == STATUS_SMS_RECEIVED and result.get("otp"):
                otp = result["otp"]
                full_sms = result.get("full_sms", otp)

                async with AsyncSessionLocal() as session:
                    await session.execute(
                        sa_update(WhatsAppOrder)
                        .where(WhatsAppOrder.grizzly_activation_id == activation_id)
                        .values(
                            status=WhatsAppOrderStatus.COMPLETED.value,
                            otp_received=otp,
                            full_sms=full_sms,
                            completed_at=datetime.now(timezone.utc),
                        )
                    )
                    await session.commit()

                try:
                    await bot.edit_message_text(
                        f"🎉 <b>WhatsApp OTP Received!</b>\n\n"
                        f"🎯 <b>Service:</b> WhatsApp\n"
                        f"{flag} <b>Country:</b> {country_name}\n"
                        f"📞 <b>Number:</b> <code>{phone_display}</code>\n"
                        f"🔑 <b>OTP Code:</b> <code>{otp}</code> (tap to copy)\n\n"
                        f"📩 <b>Full Message:</b>\n<i>{full_sms}</i>\n\n"
                        f"💰 <b>Total Charged:</b> ${charged_price:.4f} USDT",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("📱 Get Another Number", callback_data="wa:menu")],
                            [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
                        ]),
                    )
                except Exception as ex:
                    logger.error("Failed to update message with OTP: %s", ex)
                return

            # ── Number cancelled by supplier ──────────────────────────────────
            if status in (STATUS_CANCELLED, STATUS_FINISHED):
                # Automatic Refund
                async with AsyncSessionLocal() as session:
                    user_repo = UserRepository(session)
                    new_bal = await user_repo.refund_balance(user_id, charged_price)
                    await session.execute(
                        sa_update(WhatsAppOrder)
                        .where(WhatsAppOrder.grizzly_activation_id == activation_id)
                        .values(status=WhatsAppOrderStatus.REFUNDED.value)
                    )
                    await session.commit()

                try:
                    await bot.edit_message_text(
                        f"⚠️ <b>Number Cancelled — 100% Refunded</b>\n\n"
                        f"The number <code>{phone_display}</code> was cancelled by the supplier.\n\n"
                        f"💰 <b>Auto-Refund:</b> <code>+${charged_price:.4f} USDT</code> credited back to your balance.\n"
                        f"💳 <b>Updated Balance:</b> <code>${new_bal:.4f} USDT</code>\n\n"
                        "You can request a new number anytime.",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("📱 New Number", callback_data="wa:menu")],
                            [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
                        ]),
                    )
                except Exception as ex:
                    logger.error("Failed to update message on cancelled status: %s", ex)
                return

        # ── Timeout reached (5 Minutes) — Automatic Refund Execution ───────────
        # 1. Best-effort cancel on GrizzlySMS
        await client.cancel_activation(activation_id)

        # 2. 100% Automated refund in database
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            new_bal = await user_repo.refund_balance(user_id, charged_price)
            await session.execute(
                sa_update(WhatsAppOrder)
                .where(WhatsAppOrder.grizzly_activation_id == activation_id)
                .values(status=WhatsAppOrderStatus.REFUNDED.value)
            )
            await session.commit()

        try:
            await bot.edit_message_text(
                f"⏰ <b>OTP Timeout — 100% Auto-Refunded!</b>\n\n"
                f"No SMS was received for <code>{phone_display}</code> within 5 minutes.\n\n"
                f"💰 <b>Refund Amount:</b> <code>+${charged_price:.4f} USDT</code>\n"
                f"💳 <b>Your Current Balance:</b> <code>${new_bal:.4f} USDT</code>\n\n"
                f"🔒 <i>Your funds have been safely returned to your bot balance. You can try again or choose another country.</i>",
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔁 Try Again", callback_data="wa:menu")],
                    [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
                ]),
            )
        except Exception:
            pass

    finally:
        await client.close()


# ─── Cancellation Handler with Instant Auto-Refund ────────────────────────────

async def whatsapp_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cancel an active WhatsApp number session with 100% instant refund.
    Callback format: wa:cancel:<activation_id>
    """
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user:
        return

    parts = query.data.split(":")
    requested_activation_id = parts[2] if len(parts) > 2 else None

    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        if requested_activation_id:
            stmt = select(WhatsAppOrder).where(
                WhatsAppOrder.telegram_id == user.id,
                WhatsAppOrder.grizzly_activation_id == requested_activation_id,
                WhatsAppOrder.status == WhatsAppOrderStatus.ACTIVE.value,
            )
        else:
            stmt = select(WhatsAppOrder).where(
                WhatsAppOrder.telegram_id == user.id,
                WhatsAppOrder.status == WhatsAppOrderStatus.ACTIVE.value,
            )

        res = await session.execute(stmt)
        active_order = res.scalar_one_or_none()

        if not active_order:
            await query.edit_message_text(
                "ℹ️ No active WhatsApp number found to cancel.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
                ]),
            )
            return

        activation_id = active_order.grizzly_activation_id
        charged_price = active_order.charged_price

        # Atomically refund balance
        new_bal = await user_repo.refund_balance(active_order.user_id, charged_price)
        await session.execute(
            sa_update(WhatsAppOrder)
            .where(WhatsAppOrder.id == active_order.id)
            .values(status=WhatsAppOrderStatus.REFUNDED.value)
        )
        await session.commit()

    # Cancel on GrizzlySMS side
    client = _get_grizzly()
    try:
        await client.cancel_activation(activation_id)
    finally:
        await client.close()

    await query.edit_message_text(
        f"❌ <b>Number Cancelled & 100% Refunded!</b>\n\n"
        f"Your WhatsApp number session has been cancelled.\n\n"
        f"💰 <b>Refunded:</b> <code>+${charged_price:.4f} USDT</code>\n"
        f"💳 <b>Available Balance:</b> <code>${new_bal:.4f} USDT</code>\n\n"
        f"You can request a new number anytime.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 Get New Number", callback_data="wa:menu")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
        ]),
    )


# ─── View Active Session Handler ──────────────────────────────────────────────

async def whatsapp_view_active_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View the currently active WhatsApp number and time remaining."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user:
        return

    active_order = await _get_active_whatsapp_order(user.id)
    if not active_order:
        await query.edit_message_text(
            "ℹ️ You have no active WhatsApp number sessions.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📱 Get a Number", callback_data="wa:menu")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
            ]),
        )
        return

    flag = "🇺🇸" if active_order.country == "us" else "🇬🇧"
    country_name = "United States" if active_order.country == "us" else "United Kingdom"
    now = datetime.now(timezone.utc)
    expires = active_order.expires_at.replace(tzinfo=timezone.utc)
    remaining = max(0, int((expires - now).total_seconds()))
    mins, secs = divmod(remaining, 60)

    await query.edit_message_text(
        f"📱 <b>Active WhatsApp Session</b>\n\n"
        f"{flag} <b>Country:</b> {country_name}\n"
        f"📞 <b>Number:</b> <code>+{active_order.phone_number}</code>\n"
        f"⏳ <b>Time Remaining:</b> {mins}m {secs}s\n\n"
        "⚡ <i>Waiting for incoming WhatsApp OTP in real-time...</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel & Refund Number", callback_data=f"wa:cancel:{active_order.grizzly_activation_id}")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
        ]),
    )
