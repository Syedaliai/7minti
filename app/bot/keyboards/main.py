from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton


def get_main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Inline keyboard for the main hub menu."""
    buttons = [
        [
            InlineKeyboardButton("🛍 Browse Products", callback_data="catalog:1"),
            InlineKeyboardButton("💳 Deposit", callback_data="nav:deposit"),
        ],
        [
            InlineKeyboardButton("📱 WhatsApp Verification", callback_data="wa:menu"),
            InlineKeyboardButton("🤖 Codex Verification", callback_data="sms:menu:openai"),
        ],
        [
            InlineKeyboardButton("🟢 Nvidia Verification", callback_data="sms:menu:nvidia"),
            InlineKeyboardButton("📺 YouTube Verification", callback_data="sms:menu:youtube"),
        ],
        [
            InlineKeyboardButton("📦 My Orders", callback_data="orders:1"),
            InlineKeyboardButton("🔎 Search", callback_data="nav:search"),
        ],
        [
            InlineKeyboardButton("🆘 Customer Support", callback_data="nav:support"),
            InlineKeyboardButton("📖 Payment Guide", callback_data="nav:guide"),
        ],
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton("⚙️ Admin Control Panel", callback_data="admin:dashboard")])

    return InlineKeyboardMarkup(buttons)


def get_persistent_reply_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Persistent bottom keyboard for fast navigation."""
    rows = [
        [KeyboardButton("🛍 Products"), KeyboardButton("💳 Deposit")],
        [KeyboardButton("📱 WhatsApp"), KeyboardButton("🤖 Codex Verification")],
        [KeyboardButton("🟢 Nvidia Verification"), KeyboardButton("📺 YouTube")],
        [KeyboardButton("📦 My Orders"), KeyboardButton("🔎 Search")],
        [KeyboardButton("🆘 Support")],
    ]
    if is_admin:
        rows.append([KeyboardButton("⚙️ Admin")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


