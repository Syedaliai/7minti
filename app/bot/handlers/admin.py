from datetime import datetime, timezone
import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.keyboards.admin import get_admin_dashboard_keyboard, get_admin_back_keyboard
from app.config import settings
from app.db.models import OrderStatus, CheckoutStatus
from app.db.repositories import (
    UserRepository,
    OrderRepository,
    PaymentRepository,
    AuditRepository,
    CouponRepository,
)
from app.db.session import AsyncSessionLocal
from app.services.prodseller import ProdSellerService
from app.utils.money import format_currency
from app.utils.telegram import escape_html

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    """Check if Telegram user is in configured ADMIN_TELEGRAM_IDS."""
    return user_id in settings.admin_ids_set


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /admin command — show admin dashboard."""
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("⛔ Access denied. Administrator privileges required.")
        return

    await render_admin_dashboard(update, context, is_callback=False)


async def admin_dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle admin dashboard inline callback."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        await query.answer("Access denied.", show_alert=True)
        return

    await render_admin_dashboard(update, context, is_callback=True)


async def render_admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, is_callback: bool = False) -> None:
    """Gather metrics and render the full admin dashboard with all 3 live supplier balances."""
    user = update.effective_user
    prodseller_service: ProdSellerService = context.bot_data["prodseller_service"]

    start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # 1. Fetch ProdSeller balance
    ps_display = "Unavailable"
    membership_display = "N/A"
    try:
        bal_data = await prodseller_service.get_balance()
        bal_val = bal_data.get("balance", "0")
        ps_display = format_currency(bal_val, "USDT")
        membership_display = str(bal_data.get("membership", "standard")).capitalize()
    except Exception as ex:
        logger.error("Admin dashboard could not fetch ProdSeller balance: %s", ex)

    # 2. Fetch GrizzlySMS (WhatsApp) balance
    grizzly_display = "Unavailable"
    try:
        from app.services.grizzlysms import GrizzlySMSService
        grizzly = GrizzlySMSService()
        g_bal = await grizzly.get_balance()
        grizzly_display = f"${g_bal:.4f} USD"
        await grizzly.close()
    except Exception as ex:
        logger.error("Admin dashboard could not fetch GrizzlySMS balance: %s", ex)

    # 3. Fetch SMSPool (OpenAI / Nvidia / YouTube) balance
    smspool_display = "Unavailable"
    try:
        from app.services.smspool import SMSPoolService
        smspool = SMSPoolService()
        s_bal = await smspool.get_balance()
        smspool_display = f"${s_bal:.4f} USD"
        await smspool.close()
    except Exception as ex:
        logger.error("Admin dashboard could not fetch SMSPool balance: %s", ex)

    # 4. Query database metrics
    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        order_repo = OrderRepository(session)
        payment_repo = PaymentRepository(session)
        audit_repo = AuditRepository(session)

        total_users = await user_repo.count_users()
        pending_payments = await payment_repo.count_pending_payments()
        stats_today = await order_repo.get_statistics_today(start_of_day)

        # Audit log admin view
        await audit_repo.log_action(user.id, "VIEW_ADMIN_DASHBOARD")
        await session.commit()

    dashboard_text = (
        f"📊 <b>Administrator Control Center</b>\n\n"
        f"💰 <b>Live Suppliers Realtime Balances:</b>\n"
        f"• 🛒 <b>ProdSeller (Digital Store):</b> <code>{ps_display}</code> ({membership_display})\n"
        f"• 📱 <b>GrizzlySMS (WhatsApp):</b> <code>{grizzly_display}</code>\n"
        f"• ⚡ <b>SMSPool (OpenAI/Nvidia/YT):</b> <code>{smspool_display}</code>\n\n"
        f"📈 <b>Today's Business Performance:</b>\n"
        f"• Orders Delivered Today: <b>{stats_today['delivered_today']}</b>\n"
        f"• Revenue Today: <b>{format_currency(stats_today['revenue_today'])}</b>\n"
        f"• Net Profit Today: <b>{format_currency(stats_today['commission_today'])}</b>\n\n"
        f"👥 <b>System Overview:</b>\n"
        f"• Total Registered Users: <b>{total_users}</b>\n"
        f"• Pending / Verifying Payments: <b>{pending_payments}</b>\n"
        f"• Orders Requiring Review: <b>{stats_today['failed_review_total']}</b>\n"
    )

    keyboard = get_admin_dashboard_keyboard()

    if is_callback and update.callback_query:
        try:
            await update.callback_query.edit_message_text(dashboard_text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            pass
    elif update.message:
        await update.message.reply_text(dashboard_text, parse_mode="HTML", reply_markup=keyboard)


async def admin_balance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch live balances across all supplier providers (ProdSeller, GrizzlySMS, SMSPool)."""
    query = update.callback_query
    if query:
        await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        if query:
            await query.answer("⛔ Access denied. Administrator privileges required.", show_alert=True)
        elif update.message:
            await update.message.reply_text("⛔ Access denied. Administrator privileges required.")
        return

    # 1. ProdSeller
    prodseller_service: ProdSellerService = context.bot_data.get("prodseller_service")
    ps_text = "N/A"
    if prodseller_service:
        try:
            bal_data = await prodseller_service.get_balance()
            ps_text = f"${bal_data.get('balance', 0):.2f} USDT ({bal_data.get('membership', 'standard')})"
        except Exception as ex:
            ps_text = f"Error: {ex}"

    # 2. GrizzlySMS (WhatsApp)
    grizzly_text = "N/A"
    try:
        from app.services.grizzlysms import GrizzlySMSService
        grizzly = GrizzlySMSService()
        g_bal = await grizzly.get_balance()
        grizzly_text = f"${g_bal:.4f} USD"
        await grizzly.close()
    except Exception as ex:
        grizzly_text = f"Error: {ex}"

    # 3. SMSPool (OpenAI, YouTube, Nvidia)
    smspool_text = "N/A"
    try:
        from app.services.smspool import SMSPoolService
        smspool = SMSPoolService()
        s_bal = await smspool.get_balance()
        smspool_text = f"${s_bal:.4f} USD"
        await smspool.close()
    except Exception as ex:
        smspool_text = f"Error: {ex}"

    balance_text = (
        f"💰 <b>All Suppliers Live Balance Report</b>\n\n"
        f"🛒 <b>ProdSeller API:</b> <code>{ps_text}</code>\n"
        f"📱 <b>GrizzlySMS (WhatsApp):</b> <code>{grizzly_text}</code>\n"
        f"⚡ <b>SMSPool (OpenAI/Nvidia/YT):</b> <code>{smspool_text}</code>\n"
    )

    keyboard = get_admin_back_keyboard()
    if query:
        await query.edit_message_text(balance_text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.message.reply_text(balance_text, parse_mode="HTML", reply_markup=keyboard)


async def admin_refresh_cache_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Force refresh of product catalog cache."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    prodseller_service: ProdSellerService = context.bot_data["prodseller_service"]
    try:
        products = await prodseller_service.get_products(force_refresh=True)
        count = len(products)
        msg = f"✅ <b>Catalog Cache Refreshed!</b>\n\nLoaded {count} active products from ProdSeller."
    except Exception as ex:
        msg = f"❌ Error refreshing catalog: {ex}"

    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=get_admin_back_keyboard())


async def admin_problems_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View orders needing review (e.g. price changes, supplier out of stock, underpayments)."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    async with AsyncSessionLocal() as session:
        order_repo = OrderRepository(session)
        stuck_orders = await order_repo.get_stuck_processing()

    if not stuck_orders:
        text = "✅ <b>No problematic or stuck orders found.</b>"
    else:
        text = f"⚠️ <b>Found {len(stuck_orders)} orders in PROCESSING/REVIEW:</b>\n\n"
        for o in stuck_orders[:5]:
            text += f"• Order <code>{o.id}</code> (User: {o.user_id}, Qty: {o.quantity}, Status: <code>{o.status}</code>)\n"

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=get_admin_back_keyboard())


# ---------------------------------------------------------------------------
# Advanced Analytics & Sales Performance
# ---------------------------------------------------------------------------

async def admin_analytics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comprehensive financial and operational analytics dashboard."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    async with AsyncSessionLocal() as session:
        order_repo = OrderRepository(session)
        user_repo = UserRepository(session)

        metrics = await order_repo.get_comprehensive_financial_metrics()
        total_users = await user_repo.count_users()
        system_liability = await user_repo.get_total_system_liability()

    text = (
        f"📊 <b>Enterprise Business Analytics</b>\n\n"
        f"💰 <b>Financial Performance:</b>\n"
        f"• <b>Lifetime Net Profit:</b> <code>+${metrics['total_profit']:.4f} USDT</code>\n"
        f"• <b>Lifetime Gross Revenue:</b> <code>${metrics['total_revenue']:.4f} USDT</code>\n"
        f"• <b>Total Successful Sales:</b> <code>{metrics['total_orders']} orders</code>\n\n"
        f"📅 <b>Time-Based Revenue & Profit:</b>\n"
        f"• <b>Today:</b> Rev: <code>${metrics['today_revenue']:.4f}</code> | Profit: <b>+${metrics['today_profit']:.4f}</b>\n"
        f"• <b>This Month:</b> Rev: <code>${metrics['month_revenue']:.4f}</code> | Profit: <b>+${metrics['month_profit']:.4f}</b>\n\n"
        f"🎯 <b>Revenue Breakdown by Service:</b>\n"
        f"• 📱 <b>WhatsApp (GrizzlySMS):</b> {metrics['wa_count']} orders | Rev: <code>${metrics['wa_revenue']:.4f}</code> | Profit: <b>+${metrics['wa_profit']:.4f}</b>\n"
        f"• ⚡ <b>SMSPool (OpenAI/Nvidia/YT):</b> {metrics['sms_count']} orders | Rev: <code>${metrics['sms_revenue']:.4f}</code> | Profit: <b>+${metrics['sms_profit']:.4f}</b>\n"
        f"• 🛍 <b>Digital Goods (ProdSeller):</b> {metrics['digital_orders_count']} orders | Rev: <code>${metrics['digital_revenue']:.4f}</code> | Profit: <b>+${metrics['digital_profit']:.4f}</b>\n\n"
        f"👥 <b>User & Wallet Metrics:</b>\n"
        f"• Total Registered Customers: <b>{total_users}</b>\n"
        f"• Total User Unspent Balance in System: <code>${system_liability:.4f} USDT</code>\n"
    )

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=get_admin_back_keyboard())


async def admin_top_sellers_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show leaderboard of top-selling products and services."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    async with AsyncSessionLocal() as session:
        order_repo = OrderRepository(session)
        top_items = await order_repo.get_top_selling_products(limit=8)

    if not top_items:
        text = "🏆 <b>Top Sellers Ranking</b>\n\n<i>No completed product sales recorded yet. As orders are completed, top revenue generators will be ranked here.</i>"
    else:
        text = "🏆 <b>Top Selling Products & Services</b>\n\n"
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]
        for idx, item in enumerate(top_items):
            medal = medals[idx] if idx < len(medals) else "🔹"
            text += (
                f"{medal} <b>{escape_html(item['name'])}</b>\n"
                f"   • Sales: <b>{item['sales_count']} units</b>\n"
                f"   • Revenue: <code>${item['revenue']:.4f} USDT</code>\n"
                f"   • Admin Profit: <b>+${item['profit']:.4f} USDT</b>\n\n"
            )

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=get_admin_back_keyboard())


# ---------------------------------------------------------------------------
# User CRM & Profile Inspector
# ---------------------------------------------------------------------------

async def admin_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display paginated list of all users in the bot database."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    parts = query.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    limit = 6
    offset = (page - 1) * limit

    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        total_users = await user_repo.count_users()
        users_list = await user_repo.get_users_paginated(limit=limit, offset=offset)

    total_pages = max(1, (total_users + limit - 1) // limit)

    text = (
        f"👥 <b>User Management & CRM Directory</b>\n\n"
        f"Total Registered Customers: <b>{total_users}</b>\n"
        f"Showing page <b>{page}</b> of <b>{total_pages}</b>:\n\n"
        f"<i>Tap any customer button below to inspect their full profile, balance, and order history:</i>"
    )

    from app.bot.keyboards.admin import get_admin_users_pagination_keyboard
    kb = get_admin_users_pagination_keyboard(page, total_pages, users_list)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)


async def admin_user_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View full detailed profile of a single user."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    parts = query.data.split(":")
    if len(parts) < 3 or not parts[2].isdigit():
        return

    target_user_id = int(parts[2])

    async with AsyncSessionLocal() as session:
        from app.db.models import User, Order, WhatsAppOrder, SmsOrder
        from sqlalchemy import select, func

        u_res = await session.execute(select(User).where(User.id == target_user_id))
        target_user = u_res.scalar_one_or_none()
        if not target_user:
            await query.edit_message_text("❌ User record not found.", reply_markup=get_admin_back_keyboard())
            return

        # Fetch user stats
        orders_count = (await session.execute(select(func.count(Order.id)).where(Order.user_id == target_user_id))).scalar() or 0
        wa_count = (await session.execute(select(func.count(WhatsAppOrder.id)).where(WhatsAppOrder.user_id == target_user_id))).scalar() or 0
        sms_count = (await session.execute(select(func.count(SmsOrder.id)).where(SmsOrder.user_id == target_user_id))).scalar() or 0

    joined = target_user.created_at.strftime("%Y-%m-%d %H:%M UTC") if target_user.created_at else "N/A"
    status_str = "🚫 BLOCKED" if target_user.is_blocked else "🟢 ACTIVE"

    text = (
        f"👤 <b>Customer CRM Profile</b>\n\n"
        f"• <b>User DB ID:</b> <code>#{target_user.id}</code>\n"
        f"• <b>Telegram ID:</b> <code>{target_user.telegram_id}</code>\n"
        f"• <b>Username:</b> @{escape_html(target_user.username or 'None')}\n"
        f"• <b>First Name:</b> {escape_html(target_user.first_name or 'N/A')}\n"
        f"• <b>Account Status:</b> <b>{status_str}</b>\n"
        f"• <b>Joined Date:</b> <code>{joined}</code>\n\n"
        f"💳 <b>Wallet Balance:</b> <code>${target_user.balance:.4f} USDT</code>\n\n"
        f"📦 <b>Activity Summary:</b>\n"
        f"• Digital Products Purchased: <b>{orders_count}</b>\n"
        f"• WhatsApp Verifications: <b>{wa_count}</b>\n"
        f"• SMSPool Verifications: <b>{sms_count}</b>\n"
    )

    from app.bot.keyboards.admin import get_admin_user_detail_keyboard
    kb = get_admin_user_detail_keyboard(target_user.id, target_user.is_blocked)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)


async def admin_toggle_block_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle block / unblock status of a user."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    parts = query.data.split(":")
    if len(parts) < 3 or not parts[2].isdigit():
        return

    target_user_id = int(parts[2])
    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        audit_repo = AuditRepository(session)
        new_status = await user_repo.toggle_block_user(target_user_id)
        await audit_repo.log_action(user.id, "TOGGLE_USER_BLOCK", f"User {target_user_id} blocked={new_status}")
        await session.commit()

    action_label = "🚫 Blocked" if new_status else "🟢 Unblocked"
    await query.answer(f"User #{target_user_id} has been {action_label}!", show_alert=True)
    await admin_user_detail_callback(update, context)


async def admin_recent_orders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stream last 8 platform orders with profit margins."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    async with AsyncSessionLocal() as session:
        order_repo = OrderRepository(session)
        orders = await order_repo.get_recent_orders(limit=8)

    if not orders:
        text = "📦 <b>Recent Orders Stream</b>\n\n<i>No orders placed on the system yet.</i>"
    else:
        text = "📦 <b>Recent Orders Stream</b>\n\n"
        for o in orders:
            dt = o.created_at.strftime("%m-%d %H:%M") if o.created_at else ""
            product_name = o.checkout.product_name if o.checkout else f"Product {o.product_id}"
            text += (
                f"• <b>{escape_html(product_name)}</b> (x{o.quantity})\n"
                f"   Status: <code>{o.status}</code> | Paid: <code>${o.customer_amount:.2f}</code> | Profit: <b>+${o.commission_amount:.2f}</b> ({dt})\n\n"
            )

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=get_admin_back_keyboard())


# ---------------------------------------------------------------------------
# Coupon & Promo Code Management (Service-Scoped)
# ---------------------------------------------------------------------------

async def admin_coupons_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View active coupons and management options."""
    query = update.callback_query
    if query:
        await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    async with AsyncSessionLocal() as session:
        coupon_repo = CouponRepository(session)
        coupons = await coupon_repo.list_all(limit=25)

    if not coupons:
        text = (
            "🎟️ <b>Promotional Coupons Management</b>\n\n"
            "<i>No active coupons generated yet.</i>\n\n"
            "👉 Click <b>'➕ Generate New Coupon'</b> below or use the fast command:\n"
            "<code>/coupon &lt;CODE&gt; &lt;SERVICE&gt; &lt;PERCENT/FIXED&gt; &lt;VALUE&gt; [MAX_USES]</code>\n\n"
            "<b>Service Options:</b> <code>whatsapp</code>, <code>openai</code>, <code>nvidia</code>, <code>youtube</code>, <code>products</code>, <code>all</code>\n"
            "<b>Example:</b> <code>/coupon WA50 whatsapp percent 50 100</code>"
        )
    else:
        text = (
            f"🎟️ <b>Promotional Coupons ({len(coupons)} Active)</b>\n\n"
            f"<i>Coupons strictly apply only to their designated target service:</i>\n\n"
        )
        for c in coupons:
            val_display = f"{c.discount_value:.0f}% OFF" if c.discount_type == "PERCENTAGE" else f"${c.discount_value:.2f} USDT OFF"
            text += (
                f"🏷 <b>Code:</b> <code>{c.code}</code>\n"
                f"   • <b>Target Service:</b> <code>{c.service.upper()}</code>\n"
                f"   • <b>Discount:</b> <b>{val_display}</b>\n"
                f"   • <b>Redemptions:</b> <b>{c.times_used} / {c.max_uses}</b> used\n\n"
            )

    from app.bot.keyboards.admin import get_admin_coupons_keyboard
    kb = get_admin_coupons_keyboard(coupons)
    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def admin_coupon_new_service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Step 1: Choose which service this coupon strictly applies to."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    text = (
        "🎯 <b>Select Target Service for this Coupon</b>\n\n"
        "Whichever service you select below, the coupon will <b>STRICTLY work only for that service</b>:\n\n"
        "• 📱 <b>WhatsApp Only:</b> GrizzlySMS numbers\n"
        "• 🤖 <b>OpenAI / Codex Only:</b> SMSPool OpenAI\n"
        "• 🟢 <b>Nvidia Only:</b> SMSPool Nvidia\n"
        "• 📺 <b>YouTube Only:</b> SMSPool YouTube\n"
        "• 🛍 <b>Digital Products Only:</b> ProdSeller software/licenses\n"
        "• 🌐 <b>All Services:</b> Universal storewide discount"
    )
    from app.bot.keyboards.admin import get_admin_coupon_service_keyboard
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=get_admin_coupon_service_keyboard())


async def admin_coupon_set_srv_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Step 2: Guide admin to enter coupon details with pre-selected service."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    parts = query.data.split(":")
    selected_srv = parts[2] if len(parts) > 2 else "all"

    srv_name = selected_srv.upper()
    example_code = f"{selected_srv[:2].upper()}20"

    text = (
        f"📝 <b>Creating Coupon for [{srv_name}]</b>\n\n"
        f"Send the coupon creation command in chat:\n\n"
        f"<b>Format:</b>\n"
        f"<code>/coupon &lt;CODE&gt; {selected_srv} &lt;percent/fixed&gt; &lt;VALUE&gt; [MAX_USES]</code>\n\n"
        f"<b>Quick Examples:</b>\n"
        f"• <code>/coupon {example_code} {selected_srv} percent 20 50</code> (20% off for 50 users)\n"
        f"• <code>/coupon SAVE1 {selected_srv} fixed 0.10 100</code> ($0.10 USDT off for 100 users)\n"
    )

    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Services", callback_data="admin:coupon_new_service")],
        [InlineKeyboardButton("⬅️ Coupons List", callback_data="admin:coupons")],
    ])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)


async def admin_coupon_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete a coupon by ID."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    parts = query.data.split(":")
    if len(parts) < 3:
        return

    coupon_id = parts[2]
    async with AsyncSessionLocal() as session:
        coupon_repo = CouponRepository(session)
        audit_repo = AuditRepository(session)
        deleted = await coupon_repo.delete_coupon(coupon_id)
        if deleted:
            await audit_repo.log_action(user.id, "DELETE_COUPON", f"Deleted coupon {coupon_id}")
            await session.commit()

    await query.answer("🗑 Coupon deleted successfully!", show_alert=True)
    await admin_coupons_callback(update, context)


async def admin_coupon_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /coupon command for fast coupon creation.
    Usage: /coupon <CODE> <SERVICE> <percent|fixed> <VALUE> [MAX_USES]
    Example: /coupon WA50 whatsapp percent 50 100
    """
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("⛔ Access denied. Administrator privileges required.")
        return

    args = context.args or []
    if len(args) < 4:
        help_text = (
            "🎟️ <b>Fast Coupon Creation Guide</b>\n\n"
            "<b>Usage:</b>\n"
            "<code>/coupon &lt;CODE&gt; &lt;SERVICE&gt; &lt;percent|fixed&gt; &lt;VALUE&gt; [MAX_USES]</code>\n\n"
            "<b>Services:</b> <code>whatsapp</code>, <code>openai</code>, <code>nvidia</code>, <code>youtube</code>, <code>products</code>, <code>all</code>\n\n"
            "<b>Examples:</b>\n"
            "• <code>/coupon WA50 whatsapp percent 50 100</code> (50% off on WhatsApp only)\n"
            "• <code>/coupon AI20 openai percent 20 50</code> (20% off on OpenAI only)\n"
            "• <code>/coupon PROMO1 products fixed 1.00 50</code> ($1.00 off on digital goods)\n"
            "• <code>/coupon SAVE10 all percent 10 200</code> (10% off storewide)"
        )
        await update.message.reply_text(help_text, parse_mode="HTML")
        return

    code = args[0].strip().upper()
    service = args[1].strip().lower()
    disc_type_raw = args[2].strip().lower()
    val_raw = args[3].strip()
    max_uses = int(args[4]) if len(args) > 4 and args[4].isdigit() else 100

    valid_services = {"whatsapp", "openai", "nvidia", "youtube", "products", "all"}
    if service not in valid_services:
        await update.message.reply_text(f"❌ Invalid service: <code>{service}</code>. Must be one of: {', '.join(valid_services)}", parse_mode="HTML")
        return

    from decimal import Decimal, InvalidOperation
    try:
        val = Decimal(val_raw)
        if val <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await update.message.reply_text("❌ Invalid discount value. Must be a positive number.", parse_mode="HTML")
        return

    disc_type = "PERCENTAGE" if "percent" in disc_type_raw or "%" in disc_type_raw else "FIXED"
    if disc_type == "PERCENTAGE" and val > Decimal("90"):
        await update.message.reply_text("❌ Maximum percentage discount allowed is 90%.", parse_mode="HTML")
        return

    async with AsyncSessionLocal() as session:
        coupon_repo = CouponRepository(session)
        audit_repo = AuditRepository(session)

        # Check if code already exists
        existing = await coupon_repo.get_by_code(code)
        if existing:
            await update.message.reply_text(f"⚠️ A coupon with code <b>{code}</b> already exists!", parse_mode="HTML")
            return

        coupon = await coupon_repo.create_coupon(
            code=code,
            service=service,
            discount_type=disc_type,
            discount_value=val,
            max_uses=max_uses,
        )
        await audit_repo.log_action(user.id, "CREATE_COUPON", f"Created coupon {code} for {service} value={val}")
        await session.commit()

    val_display = f"{val:.0f}% OFF" if disc_type == "PERCENTAGE" else f"${val:.4f} USDT OFF"
    success_text = (
        f"🎉 <b>Coupon Created Successfully!</b>\n\n"
        f"🏷 <b>Promo Code:</b> <code>{coupon.code}</code>\n"
        f"🎯 <b>Target Service:</b> <code>{coupon.service.upper()}</code>\n"
        f"💰 <b>Discount:</b> <b>{val_display}</b>\n"
        f"👥 <b>Max Redemptions:</b> <b>{coupon.max_uses} uses</b>\n\n"
        f"⚡ <i>This code will strictly work ONLY on {coupon.service.upper()}!</i>"
    )
    from app.bot.keyboards.admin import get_admin_back_keyboard
    await update.message.reply_text(success_text, parse_mode="HTML", reply_markup=get_admin_back_keyboard())


async def admin_set_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Configure mandatory channel subscription.
    Usage: /setchannel <@channel_username_or_id> [link]
    Example: /setchannel @mychannel https://t.me/mychannel
    To disable: /setchannel none
    """
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("⛔ Access denied. Administrator privileges required.")
        return

    args = context.args or []
    if not args:
        current_chan = settings.REQUIRED_CHANNEL_ID or "Disabled (None)"
        current_link = settings.REQUIRED_CHANNEL_LINK or "Auto"
        help_text = (
            "📢 <b>Mandatory Channel Subscription Settings</b>\n\n"
            f"• <b>Current Channel:</b> <code>{current_chan}</code>\n"
            f"• <b>Invite Link:</b> <code>{current_link}</code>\n\n"
            "<b>To set a new channel:</b>\n"
            "<code>/setchannel &lt;@channel_username_or_id&gt; [invite_link]</code>\n\n"
            "<b>Examples:</b>\n"
            "• <code>/setchannel @MyOfficialChannel https://t.me/MyOfficialChannel</code>\n"
            "• <code>/setchannel -100123456789 https://t.me/+AbCdEfGh</code>\n"
            "• <code>/setchannel none</code> (To disable force-join)\n\n"
            "⚠️ <i>Important: Make sure your bot is added as an <b>Admin</b> in the channel so it can verify subscribers!</i>"
        )
        await update.message.reply_text(help_text, parse_mode="HTML")
        return

    target_channel = args[0].strip()
    target_link = args[1].strip() if len(args) > 1 else None

    if target_channel.lower() in ("none", "off", "disable", "disabled"):
        settings.REQUIRED_CHANNEL_ID = None
        settings.REQUIRED_CHANNEL_LINK = None
        await update.message.reply_text("✅ <b>Mandatory channel subscription has been disabled.</b>", parse_mode="HTML")
        return

    settings.REQUIRED_CHANNEL_ID = target_channel
    if target_link:
        settings.REQUIRED_CHANNEL_LINK = target_link
    elif target_channel.startswith("@"):
        settings.REQUIRED_CHANNEL_LINK = f"https://t.me/{target_channel[1:]}"

    success_msg = (
        "🎉 <b>Mandatory Channel Subscription Configured!</b>\n\n"
        f"📢 <b>Channel:</b> <code>{settings.REQUIRED_CHANNEL_ID}</code>\n"
        f"🔗 <b>Link:</b> <code>{settings.REQUIRED_CHANNEL_LINK}</code>\n\n"
        "🔒 <i>All new users must now join this channel before accessing the bot.</i>\n\n"
        "⚠️ <b>Reminder:</b> Add your bot (<code>@sevenminti_bot</code>) as an <b>Administrator</b> in your channel!"
    )
    from app.bot.keyboards.admin import get_admin_back_keyboard
    await update.message.reply_text(success_msg, parse_mode="HTML", reply_markup=get_admin_back_keyboard())
