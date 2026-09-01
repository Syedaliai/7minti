from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def get_checkout_keyboard(checkout_id: str) -> InlineKeyboardMarkup:
    """Keyboard for checkout screen with 'I Have Paid' and cancel actions."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ I Have Paid", callback_data=f"paid:{checkout_id}"),
        ],
        [
            InlineKeyboardButton("❌ Cancel Order", callback_data="nav:home"),
            InlineKeyboardButton("🆘 Support", callback_data="nav:support"),
        ],
    ])


def get_payment_verifying_keyboard(checkout_id: str) -> InlineKeyboardMarkup:
    """Keyboard shown when payment is undergoing verification."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Refresh Verification", callback_data=f"paid:{checkout_id}"),
        ],
        [
            InlineKeyboardButton("🆘 Contact Support", callback_data="nav:support"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
        ],
    ])


def get_orders_page_keyboard(
    orders: list,
    current_page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    """Paginated order history keyboard."""
    buttons = []
    for order in orders:
        product_name = order.checkout.product_name if order.checkout else f"Product {order.product_id[:8]}"
        status_icon = "✅" if order.status == "DELIVERED" else "⏳"
        buttons.append([
            InlineKeyboardButton(
                f"{status_icon} {product_name} ({order.status})",
                callback_data=f"order_view:{order.id}",
            )
        ])

    nav = []
    if current_page > 1:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"orders:{current_page - 1}"))
    if total_pages > 0:
        nav.append(InlineKeyboardButton(f"📄 {current_page}/{total_pages}", callback_data="noop"))
    if current_page < total_pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"orders:{current_page + 1}"))

    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")])
    return InlineKeyboardMarkup(buttons)


def get_order_detail_keyboard(order_id: str, is_delivered: bool = False) -> InlineKeyboardMarkup:
    """Actions for a single order."""
    buttons = []
    if is_delivered:
        buttons.append([InlineKeyboardButton("🔑 View Credentials", callback_data=f"order_keys:{order_id}")])
    buttons.append([
        InlineKeyboardButton("⬅️ Back to Orders", callback_data="orders:1"),
        InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
    ])
    return InlineKeyboardMarkup(buttons)
