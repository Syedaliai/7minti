from decimal import Decimal
from typing import Any, Dict, List
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.pricing import PricingService
from app.utils.money import format_currency


def get_catalog_page_keyboard(
    products: List[Dict[str, Any]],
    current_page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    """Paginated list of products."""
    keyboard: List[List[InlineKeyboardButton]] = []

    for p in products:
        prod_id = str(p.get("id"))
        prod_name = str(p.get("name", "Product"))
        supplier_price = p.get("price", 0)
        customer_price = PricingService.calculate_unit_price(supplier_price)
        stock_badge = "✅" if p.get("inStock", True) else "❌"

        btn_text = f"{stock_badge} {prod_name} — {format_currency(customer_price)}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"prod:{prod_id}")])

    # Pagination navigation controls
    nav_buttons = []
    if current_page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"catalog:{current_page - 1}"))
    nav_buttons.append(InlineKeyboardButton(f"📄 {current_page}/{total_pages}", callback_data="noop"))
    if current_page < total_pages:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"catalog:{current_page + 1}"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([
        InlineKeyboardButton("🔎 Search", callback_data="nav:search"),
        InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
    ])

    return InlineKeyboardMarkup(keyboard)


def get_product_detail_keyboard(
    product_id: str,
    quantity: int = 1,
    in_stock: bool = True,
) -> InlineKeyboardMarkup:
    """Product detail keyboard with quantity selector and Buy button."""
    keyboard = []

    if in_stock:
        # Quantity controls: [-] [qty] [+]
        keyboard.append([
            InlineKeyboardButton("➖", callback_data=f"qty_dec:{product_id}:{quantity}"),
            InlineKeyboardButton(f"Qty: {quantity}", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"qty_inc:{product_id}:{quantity}"),
        ])
        # Buy button
        keyboard.append([
            InlineKeyboardButton(f"⚡ Buy Now ({quantity})", callback_data=f"buy:{product_id}:{quantity}")
        ])
    else:
        keyboard.append([InlineKeyboardButton("❌ Out of Stock", callback_data="noop")])

    keyboard.append([
        InlineKeyboardButton("⬅️ Back to Catalog", callback_data="catalog:1"),
        InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
    ])

    return InlineKeyboardMarkup(keyboard)
