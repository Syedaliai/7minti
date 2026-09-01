import logging
from decimal import Decimal
from telegram import Update
from telegram.ext import ContextTypes

from app.db.repositories import CouponRepository, UserRepository
from app.db.session import AsyncSessionLocal
from app.utils.telegram import escape_html

logger = logging.getLogger(__name__)


async def promo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /promo or /coupon command for customers to activate a discount code.
    Usage: /promo <CODE>
    """
    user = update.effective_user
    if not user:
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "🎟️ <b>Have a Promo Code?</b>\n\n"
            "Usage: <code>/promo &lt;CODE&gt;</code>\n"
            "Example: <code>/promo WA50</code>\n\n"
            "👉 <i>Enter your promo code above to activate instant discounts on your next number or product purchase!</i>",
            parse_mode="HTML",
        )
        return

    code = args[0].strip().upper()

    async with AsyncSessionLocal() as session:
        coupon_repo = CouponRepository(session)
        user_repo = UserRepository(session)
        db_user = await user_repo.upsert_user(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )

        # Validate code existence
        coupon = await coupon_repo.get_by_code(code)
        if not coupon:
            await update.message.reply_text("❌ <b>Invalid Promo Code.</b> Please check the code and try again.", parse_mode="HTML")
            return

        if not coupon.is_active or coupon.times_used >= coupon.max_uses:
            await update.message.reply_text("❌ <b>This promo code is expired or reached its maximum usage limit.</b>", parse_mode="HTML")
            return

    # Store active coupon in user_data session
    context.user_data["active_promo_code"] = code

    service_labels = {
        "whatsapp": "📱 WhatsApp Verification Only",
        "openai": "🤖 OpenAI / Codex Verification Only",
        "nvidia": "🟢 Nvidia Verification Only",
        "youtube": "📺 YouTube Verification Only",
        "products": "🛍 Digital Store Products Only",
        "all": "🌐 All Bot Services (Storewide)",
    }
    target_label = service_labels.get(coupon.service.lower(), coupon.service.upper())
    discount_display = f"{coupon.discount_value:.0f}% OFF" if coupon.discount_type == "PERCENTAGE" else f"${coupon.discount_value:.4f} USDT OFF"

    success_msg = (
        f"🎉 <b>Promo Code <code>{coupon.code}</code> Activated!</b>\n\n"
        f"💰 <b>Discount:</b> <b>{discount_display}</b>\n"
        f"🎯 <b>Eligible Service:</b> <b>{target_label}</b>\n\n"
        f"⚡ <i>Your discount will be automatically applied when you purchase an eligible item!</i>"
    )
    await update.message.reply_text(success_msg, parse_mode="HTML")
