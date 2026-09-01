import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from app.bot.middleware.rate_limit import general_rate_limiter
from app.services.pricing import PricingService
from app.services.prodseller import ProdSellerService
from app.utils.money import format_currency
from app.utils.telegram import escape_html

logger = logging.getLogger(__name__)


async def search_init_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to type a keyword search term."""
    query = update.callback_query
    if query:
        await query.answer()

    # Set conversational state in user_data
    context.user_data["awaiting_search"] = True

    search_prompt = (
        "🔎 <b>Search Catalog</b>\n\n"
        "Please type the name or keyword of the digital product you're looking for (e.g. <code>ChatGPT</code>, <code>Canva</code>, <code>Claude</code>, <code>VPN</code>, <code>Netflix</code>):"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Cancel Search", callback_data="nav:home")]
    ])

    if query:
        await query.edit_message_text(search_prompt, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.message.reply_text(search_prompt, parse_mode="HTML", reply_markup=keyboard)


async def search_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process incoming text search query from user."""
    if not context.user_data.get("awaiting_search"):
        return

    query_text = update.message.text.strip().lower()
    if len(query_text) < 2:
        await update.message.reply_text("Please enter at least 2 characters to search.")
        return

    context.user_data["awaiting_search"] = False
    prodseller_service: ProdSellerService = context.bot_data["prodseller_service"]

    try:
        products = await prodseller_service.get_products()
    except Exception as ex:
        logger.error("Failed to search products: %s", ex)
        await update.message.reply_text("⚠️ Unable to search catalog right now.")
        return

    # Filter matching products
    matches = []
    for p in products:
        name = str(p.get("name", "")).lower()
        desc = str(p.get("description", "")).lower()
        if query_text in name or query_text in desc:
            matches.append(p)

    if not matches:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛍 Browse All", callback_data="catalog:1")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
        ])
        await update.message.reply_text(
            f"❌ No products found matching '<b>{escape_html(query_text)}</b>'.",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    # Build results keyboard
    keyboard = []
    for p in matches[:10]:
        prod_id = str(p.get("id"))
        prod_name = str(p.get("name", "Product"))
        supplier_price = p.get("price", 0)
        customer_price = PricingService.calculate_unit_price(supplier_price)
        stock_badge = "✅" if p.get("inStock", True) else "❌"

        keyboard.append([
            InlineKeyboardButton(
                f"{stock_badge} {prod_name} — {format_currency(customer_price)}",
                callback_data=f"prod:{prod_id}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton("🔎 Search Again", callback_data="nav:search"),
        InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
    ])

    await update.message.reply_text(
        f"🔎 <b>Search Results for '{escape_html(query_text)}'</b> ({len(matches)} found):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
