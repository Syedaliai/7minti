import logging
import math
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.keyboards.products import get_catalog_page_keyboard, get_product_detail_keyboard
from app.bot.middleware.rate_limit import general_rate_limiter
from app.services.pricing import PricingService
from app.services.prodseller import ProdSellerService, ProdSellerAPIError
from app.utils.money import format_currency
from app.utils.telegram import escape_html, clean_supplier_text

logger = logging.getLogger(__name__)

PAGE_SIZE = 6


async def catalog_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle browsing catalog with pagination."""
    query = update.callback_query
    if query:
        await query.answer()

    user = update.effective_user
    if user and not general_rate_limiter.is_allowed(user.id):
        if query:
            await query.answer("Please slow down...", show_alert=True)
        return

    # Extract requested page number
    page = 1
    if query and query.data and ":" in query.data:
        try:
            page = int(query.data.split(":")[1])
        except (ValueError, IndexError):
            page = 1

    prodseller_service: ProdSellerService = context.bot_data["prodseller_service"]

    try:
        products = await prodseller_service.get_products()
    except Exception as ex:
        logger.error("Failed to fetch products for catalog: %s", ex)
        err_msg = "⚠️ <i>Unable to load product catalog right now. Please try again in a moment.</i>"
        if query:
            await query.edit_message_text(err_msg, parse_mode="HTML")
        else:
            await update.message.reply_text(err_msg, parse_mode="HTML")
        return

    if not products:
        no_prod_msg = "📦 <b>No products currently available.</b>\nPlease check back soon!"
        if query:
            await query.edit_message_text(no_prod_msg, parse_mode="HTML")
        else:
            await update.message.reply_text(no_prod_msg, parse_mode="HTML")
        return

    total_products = len(products)
    total_pages = max(1, math.ceil(total_products / PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    current_page_products = products[start_idx:end_idx]

    catalog_text = (
        f"🛍 <b>Browse Products</b> (Page {page}/{total_pages})\n\n"
        f"Click on any product to view details, specifications, and instant checkout:"
    )

    catalog_keyboard = get_catalog_page_keyboard(
        products=current_page_products,
        current_page=page,
        total_pages=total_pages,
    )

    if query:
        await query.edit_message_text(catalog_text, parse_mode="HTML", reply_markup=catalog_keyboard)
    else:
        await update.message.reply_text(catalog_text, parse_mode="HTML", reply_markup=catalog_keyboard)


async def product_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle displaying single product detail view."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) < 2:
        return
    product_id = parts[1]

    prodseller_service: ProdSellerService = context.bot_data["prodseller_service"]

    try:
        # Fetch fresh product info
        product = await prodseller_service.get_product(product_id)
    except Exception as ex:
        logger.error("Failed to fetch product %s details: %s", product_id, ex)
        await query.answer("Could not load product details.", show_alert=True)
        return

    prod_name = escape_html(product.get("name", "Digital Product"))
    description = clean_supplier_text(product.get("description", "No description provided."))
    supplier_price = product.get("price", 0)
    customer_price = PricingService.calculate_unit_price(supplier_price)
    in_stock = product.get("inStock", True)
    stock_count = product.get("stock")
    delivery_type = escape_html(product.get("delivery", {}).get("type", "Instant Key")) if isinstance(product.get("delivery"), dict) else "Instant Key"

    stock_display = "✅ In Stock" if in_stock else "❌ Out of Stock"
    if stock_count is not None:
        stock_display += f" ({stock_count} units available)"

    detail_text = (
        f"📦 <b>{prod_name}</b>\n\n"
        f"{description}\n\n"
        f"💰 <b>Price:</b> <code>{format_currency(customer_price)}</code>\n"
        f"⚡ <b>Delivery:</b> {delivery_type}\n"
        f"📊 <b>Availability:</b> {stock_display}\n\n"
        f"Select quantity and click <b>Buy Now</b> to proceed to payment:"
    )

    await query.edit_message_text(
        detail_text,
        parse_mode="HTML",
        reply_markup=get_product_detail_keyboard(
            product_id=product_id,
            quantity=1,
            in_stock=in_stock,
        ),
    )


async def quantity_adjust_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incrementing or decrementing checkout quantity on product view."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) < 3:
        return

    action = parts[0]
    product_id = parts[1]
    current_qty = int(parts[2])

    if action == "qty_inc":
        new_qty = min(50, current_qty + 1)
    elif action == "qty_dec":
        new_qty = max(1, current_qty - 1)
    else:
        new_qty = current_qty

    if new_qty == current_qty:
        return

    prodseller_service: ProdSellerService = context.bot_data["prodseller_service"]
    try:
        product = await prodseller_service.get_product(product_id)
    except Exception:
        return

    prod_name = escape_html(product.get("name", "Digital Product"))
    description = clean_supplier_text(product.get("description", "No description provided."))
    supplier_price = product.get("price", 0)
    customer_unit_price = PricingService.calculate_unit_price(supplier_price)
    expected_total = PricingService.calculate_total(supplier_price, new_qty)
    in_stock = product.get("inStock", True)
    delivery_type = escape_html(product.get("delivery", {}).get("type", "Instant Key")) if isinstance(product.get("delivery"), dict) else "Instant Key"

    detail_text = (
        f"📦 <b>{prod_name}</b>\n\n"
        f"{description}\n\n"
        f"💰 <b>Unit Price:</b> <code>{format_currency(customer_unit_price)}</code>\n"
        f"🧾 <b>Total ({new_qty}x):</b> <code>{format_currency(expected_total)}</code>\n"
        f"⚡ <b>Delivery:</b> {delivery_type}\n\n"
        f"Select quantity and click <b>Buy Now</b> to proceed to payment:"
    )

    await query.edit_message_text(
        detail_text,
        parse_mode="HTML",
        reply_markup=get_product_detail_keyboard(
            product_id=product_id,
            quantity=new_qty,
            in_stock=in_stock,
        ),
    )
