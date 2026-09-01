import logging
from decimal import Decimal, InvalidOperation
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from app.bot.middleware.rate_limit import verify_rate_limiter
from app.config import settings
from app.db.models import CheckoutStatus, Payment
from app.db.repositories import PaymentRepository, UserRepository
from app.db.session import AsyncSessionLocal
from app.services.binance import BinanceService
from app.utils.money import to_decimal
from app.utils.telegram import escape_html

logger = logging.getLogger(__name__)


async def deposit_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display Deposit Methods menu (Image 2)."""
    text = (
        "💳 <b>Deposit Methods</b>\n"
        "Choose a payment method:"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔶 Binance Pay", callback_data="deposit:binance_pay")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="nav:home")],
    ])

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    elif update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def deposit_binance_pay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user for deposit amount and instruct to send to Binance Pay UID (Image 3)."""
    query = update.callback_query
    await query.answer()

    binance_uid = settings.BINANCE_UID or "1254494426"

    text = (
        f"🔶 <b>Binance Pay</b>\n\n"
        f"Send USDT to Binance Pay UID: <code>{escape_html(binance_uid)}</code>\n"
        f"Then send me the Order ID.\n\n"
        f"💵 Minimum: $0.1\n\n"
        f"💵 How much did you send? Enter the amount in USD (e.g. 50):"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✖ Cancel", callback_data="deposit:cancel")]
    ])

    # Mark user state
    context.user_data["awaiting_deposit_amount"] = True
    context.user_data.pop("awaiting_deposit_order_id", None)
    context.user_data.pop("deposit_amount", None)

    await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def deposit_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel deposit operation."""
    query = update.callback_query
    await query.answer()

    context.user_data.pop("awaiting_deposit_amount", None)
    context.user_data.pop("awaiting_deposit_order_id", None)
    context.user_data.pop("deposit_amount", None)

    await query.edit_message_text(
        "❌ <b>Deposit cancelled.</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")]
        ]),
    )


async def handle_deposit_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text input during deposit flow (Amount or Order ID). Returns True if handled."""
    user = update.effective_user
    if not user:
        return False

    raw_text = update.message.text.strip()

    # Step 1: User enters amount
    if context.user_data.get("awaiting_deposit_amount"):
        cleaned_text = raw_text.replace("$", "").replace("USDT", "").replace("usdt", "").strip()
        try:
            amount = Decimal(cleaned_text)
            if amount < Decimal("0.1"):
                await update.message.reply_text(
                    "⚠️ <b>Minimum deposit is $0.1.</b> Please enter a valid amount in USD:",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✖ Cancel", callback_data="deposit:cancel")]]),
                )
                return True
        except (InvalidOperation, ValueError):
            await update.message.reply_text(
                "❌ <b>Invalid amount.</b> Please enter a numeric amount (e.g. <code>50</code> or <code>10.5</code>):",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✖ Cancel", callback_data="deposit:cancel")]]),
            )
            return True

        context.user_data.pop("awaiting_deposit_amount", None)
        context.user_data["awaiting_deposit_order_id"] = True
        context.user_data["deposit_amount"] = str(amount)

        binance_uid = settings.BINANCE_UID or "1254494426"
        prompt_text = (
            f"📝 <b>Submit Binance Pay Order ID</b>\n\n"
            f"💰 Amount Sent: <code>{amount} USDT</code>\n"
            f"🆔 Binance Pay UID: <code>{escape_html(binance_uid)}</code>\n\n"
            f"Please copy and paste your <b>Binance Pay Order ID</b> (from your Binance Pay transaction details):\n"
            f"<i>(Example: 28941829482910)</i>"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✖ Cancel", callback_data="deposit:cancel")]
        ])
        await update.message.reply_text(prompt_text, parse_mode="HTML", reply_markup=keyboard)
        return True

    # Step 2: User enters Binance Pay Order ID
    if context.user_data.get("awaiting_deposit_order_id"):
        order_id = raw_text
        expected_amount_str = context.user_data.get("deposit_amount", "0")
        expected_amount = Decimal(expected_amount_str)

        # Rate limiting
        if not verify_rate_limiter.is_allowed(user.id):
            await update.message.reply_text(
                "⏳ <b>Verification rate limit reached.</b> Please wait a few seconds before trying again.",
                parse_mode="HTML",
            )
            return True

        if len(order_id) < 6:
            await update.message.reply_text(
                "❌ <b>Invalid Order ID format.</b> Please enter a valid Binance Pay Order ID:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✖ Cancel", callback_data="deposit:cancel")]]),
            )
            return True

        verifying_msg = await update.message.reply_text(
            f"🔍 <b>Verifying Binance Pay Order ID...</b>\n"
            f"Order ID: <code>{escape_html(order_id)}</code>\n\n"
            f"<i>Checking transaction with Binance API...</i>",
            parse_mode="HTML",
        )

        binance_service: BinanceService = context.bot_data["binance_service"]

        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            payment_repo = PaymentRepository(session)

            db_user = await user_repo.get_by_telegram_id(user.id)
            if not db_user:
                await verifying_msg.edit_text("User session not found. Please restart /start.")
                return True

            # Anti-tamper & Anti-replay check: Has this Order ID been used anywhere before?
            existing_payment = await payment_repo.get_by_txid(order_id)
            if existing_payment:
                logger.warning("Duplicate Binance Pay Order ID submitted: %s by user %s", order_id, user.id)
                await verifying_msg.edit_text(
                    "❌ <b>Duplicate Payment Detected!</b>\n"
                    "This Binance Pay Order ID has already been redeemed and approved in our system. Re-submitting or manipulating Order IDs is strictly prevented.",
                    parse_mode="HTML",
                )
                return True

            # Query Binance Pay API for authoritative transaction data
            matching_pay_tx = None
            try:
                pay_records = await binance_service.get_pay_transactions(limit=100)
                for prec in pay_records:
                    p_order_id = str(prec.get("orderId", "")).strip()
                    p_tx_id = str(prec.get("transactionId", "")).strip()
                    if order_id.lower() in (p_order_id.lower(), p_tx_id.lower()):
                        matching_pay_tx = prec
                        break
            except Exception as ex:
                logger.error("Failed to query Binance Pay API for Order ID %s: %s", order_id, ex)
                await verifying_msg.edit_text(
                    "⚠️ <b>Binance API Error:</b> Unable to connect to Binance at the moment. Please try again shortly.",
                    parse_mode="HTML",
                )
                return True

            if not matching_pay_tx:
                logger.info("Binance Pay Order ID %s not found in records", order_id)
                await verifying_msg.edit_text(
                    "❌ <b>Order ID not found on Binance!</b>\n\n"
                    "No Binance Pay transaction with this Order ID was found in our account. Please make sure:\n"
                    f"1. You sent USDT to Binance Pay UID: <code>{escape_html(settings.BINANCE_UID or '1254494426')}</code>\n"
                    "2. The transaction has completed in Binance\n"
                    "3. You pasted the correct Order ID from transaction details",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✖ Cancel", callback_data="deposit:cancel")]]),
                )
                return True

            # Validate currency
            dep_coin = str(matching_pay_tx.get("currency", "USDT")).upper()
            if dep_coin != "USDT":
                await verifying_msg.edit_text(
                    f"❌ <b>Currency Mismatch:</b> Received payment in {dep_coin}, but expected USDT.",
                    parse_mode="HTML",
                )
                return True

            # Validate status
            pay_status = str(matching_pay_tx.get("status", "")).upper()
            if pay_status not in ("SUCCESS", "PAID", "COMPLETED", "1"):
                await verifying_msg.edit_text(
                    f"⚠️ <b>Transaction Pending:</b> Binance Pay status is <code>{escape_html(pay_status)}</code>. Please wait until completed and try again.",
                    parse_mode="HTML",
                )
                return True

            # Validate receiver UID
            receiver_info = matching_pay_tx.get("receiverInfo") or {}
            receiver_uid = str(receiver_info.get("binanceId") or receiver_info.get("accountId") or "").strip()
            if settings.BINANCE_UID and receiver_uid and receiver_uid != settings.BINANCE_UID.strip():
                logger.warning("Binance Pay receiver UID mismatch: received %s, expected %s", receiver_uid, settings.BINANCE_UID)
                await verifying_msg.edit_text(
                    "❌ <b>UID Mismatch:</b> Payment was not sent to our designated Binance Pay UID.",
                    parse_mode="HTML",
                )
                return True

            received_amount = to_decimal(matching_pay_tx.get("amount", "0"))

            # Check for underpayment if expected amount was specified
            if expected_amount > Decimal("0") and received_amount < expected_amount:
                await verifying_msg.edit_text(
                    f"⚠️ <b>Underpayment Detected!</b>\n\n"
                    f"Expected: <code>{expected_amount} USDT</code>\n"
                    f"Received: <code>{received_amount} USDT</code>\n\n"
                    f"Please contact @{settings.SUPPORT_USERNAME} for assistance.",
                    parse_mode="HTML",
                )
                return True

            # Create Payment record and credit user balance
            import uuid, json
            from datetime import datetime, timezone
            payment_record = Payment(
                id=str(uuid.uuid4()),
                checkout_id=None,
                user_id=db_user.id,
                txid=order_id,
                coin="USDT",
                network="BINANCE_PAY",
                expected_amount=expected_amount if expected_amount > Decimal("0") else received_amount,
                received_amount=received_amount,
                status="PAID",
                binance_status=pay_status,
                verified_at=datetime.now(timezone.utc),
                raw_reference_metadata=json.dumps(matching_pay_tx),
            )
            session.add(payment_record)
            new_balance = await user_repo.add_balance(db_user.id, received_amount)
            await session.commit()

            # Clear state
            context.user_data.pop("awaiting_deposit_order_id", None)
            context.user_data.pop("deposit_amount", None)

            # Success confirmation!
            success_text = (
                f"🎉 <b>Binance Pay Deposit Approved!</b>\n\n"
                f"✅ <b>Status:</b> Confirmed & Verified via Binance Pay\n"
                f"🆔 <b>Order ID:</b> <code>{escape_html(order_id)}</code>\n"
                f"💰 <b>Amount Credited:</b> <code>+{received_amount} USDT</code>\n"
                f"💳 <b>Current Balance:</b> <code>{new_balance} USDT</code>\n\n"
                f"🔒 <i>Funds are now instantly available for all orders & SMS services.</i>"
            )

            nav_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📱 WhatsApp Numbers", callback_data="wa:menu")],
                [InlineKeyboardButton("🛍 Browse Catalog", callback_data="catalog:1")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
            ])

            await verifying_msg.edit_text(success_text, parse_mode="HTML", reply_markup=nav_keyboard)
            return True

    return False
