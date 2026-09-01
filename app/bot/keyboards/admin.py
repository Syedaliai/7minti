from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from typing import List


def get_admin_dashboard_keyboard() -> InlineKeyboardMarkup:
    """Admin control panel main hub menu."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Realtime Analytics", callback_data="admin:analytics"),
            InlineKeyboardButton("🏆 Top Sellers Ranking", callback_data="admin:top_sellers"),
        ],
        [
            InlineKeyboardButton("👥 User CRM & Directory", callback_data="admin:users:1"),
            InlineKeyboardButton("🎟️ Coupons Management", callback_data="admin:coupons"),
        ],
        [
            InlineKeyboardButton("💰 Supplier Balances", callback_data="admin:balance"),
            InlineKeyboardButton("📦 Recent Orders Stream", callback_data="admin:recent_orders"),
        ],
        [
            InlineKeyboardButton("⚠️ Problem Orders", callback_data="admin:problems"),
            InlineKeyboardButton("🔄 Refresh Catalog Cache", callback_data="admin:refresh_cache"),
        ],
        [
            InlineKeyboardButton("🏠 Exit Admin Mode", callback_data="nav:home"),
        ],
    ])


def get_admin_back_keyboard() -> InlineKeyboardMarkup:
    """Quick back button to admin dashboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Admin Dashboard", callback_data="admin:dashboard")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
    ])


def get_admin_users_pagination_keyboard(page: int, total_pages: int, users: list) -> InlineKeyboardMarkup:
    """Keyboard for browsing users with pagination and quick inspect buttons."""
    buttons = []
    
    # 2 user inspect buttons per row
    row = []
    for u in users:
        u_label = f"👤 @{u.username}" if u.username else f"👤 ID:{u.telegram_id}"
        row.append(InlineKeyboardButton(u_label, callback_data=f"admin:user:{u.id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Navigation row
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin:users:{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page}/{max(1, total_pages)}", callback_data="noop"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin:users:{page + 1}"))
    buttons.append(nav_row)

    buttons.append([InlineKeyboardButton("⬅️ Admin Dashboard", callback_data="admin:dashboard")])
    return InlineKeyboardMarkup(buttons)


def get_admin_user_detail_keyboard(user_id: int, is_blocked: bool) -> InlineKeyboardMarkup:
    """Actions for managing a specific user."""
    block_btn_text = "🟢 Unblock User" if is_blocked else "🚫 Block User"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(block_btn_text, callback_data=f"admin:toggle_block:{user_id}")],
        [InlineKeyboardButton("👥 Back to Users List", callback_data="admin:users:1")],
        [InlineKeyboardButton("⬅️ Admin Dashboard", callback_data="admin:dashboard")],
    ])


def get_admin_coupons_keyboard(coupons: list) -> InlineKeyboardMarkup:
    """Keyboard for coupon management with deletion options."""
    buttons = [
        [InlineKeyboardButton("➕ Generate New Coupon", callback_data="admin:coupon_new_service")],
    ]
    for c in coupons:
        val_str = f"{c.discount_value:.0f}%" if c.discount_type == "PERCENTAGE" else f"${c.discount_value:.2f}"
        btn_label = f"🗑 Delete [{c.code}] ({c.service.upper()} - {val_str})"
        buttons.append([InlineKeyboardButton(btn_label, callback_data=f"admin:coupon_delete:{c.id}")])

    buttons.append([InlineKeyboardButton("⬅️ Admin Dashboard", callback_data="admin:dashboard")])
    return InlineKeyboardMarkup(buttons)


def get_admin_coupon_service_keyboard() -> InlineKeyboardMarkup:
    """Select which service this coupon strictly applies to."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📱 WhatsApp Only", callback_data="admin:coupon_set_srv:whatsapp"),
            InlineKeyboardButton("🤖 OpenAI / Codex Only", callback_data="admin:coupon_set_srv:openai"),
        ],
        [
            InlineKeyboardButton("🟢 Nvidia Only", callback_data="admin:coupon_set_srv:nvidia"),
            InlineKeyboardButton("📺 YouTube Only", callback_data="admin:coupon_set_srv:youtube"),
        ],
        [
            InlineKeyboardButton("🛍 Digital Products Only", callback_data="admin:coupon_set_srv:products"),
            InlineKeyboardButton("🌐 All Services", callback_data="admin:coupon_set_srv:all"),
        ],
        [InlineKeyboardButton("🔙 Cancel", callback_data="admin:coupons")],
    ])
