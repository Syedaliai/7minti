import logging
from telegram.request import HTTPXRequest
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from app.config import settings
from app.db.repositories import OrderRepository, CheckoutRepository, AuditRepository
from app.db.session import init_db, AsyncSessionLocal
from app.logging_config import setup_logging
from app.services.binance import BinanceService
from app.services.encryption import encryption_service
from app.services.prodseller import ProdSellerService
from app.services.reconciliation import ReconciliationService

# Handlers
from app.bot.handlers.start import start_command, home_callback, payment_guide_callback
from app.bot.handlers.products import catalog_callback, product_detail_callback, quantity_adjust_callback
from app.bot.handlers.search import search_init_callback, search_query_handler
from app.bot.handlers.checkout import buy_product_callback
from app.bot.handlers.payment import paid_button_callback, txid_message_handler
from app.bot.handlers.orders import my_orders_callback, order_view_callback, order_keys_callback
from app.bot.handlers.support import support_callback
from app.bot.handlers.deposit import (
    deposit_menu_callback,
    deposit_binance_pay_callback,
    deposit_cancel_callback,
    handle_deposit_text_message,
)
from app.bot.handlers.admin import (
    admin_command,
    admin_dashboard_callback,
    admin_balance_callback,
    admin_refresh_cache_callback,
    admin_problems_callback,
    admin_analytics_callback,
    admin_top_sellers_callback,
    admin_users_callback,
    admin_user_detail_callback,
    admin_toggle_block_callback,
    admin_recent_orders_callback,
    admin_coupons_callback,
    admin_coupon_new_service_callback,
    admin_coupon_set_srv_callback,
    admin_coupon_delete_callback,
    admin_coupon_command,
    admin_set_channel_command,
)
from app.bot.handlers.promo import promo_command
from app.bot.handlers.channel_guard import (
    force_sub_verify_callback,
    is_user_channel_member,
    send_force_sub_prompt,
    forward_channel_post_handler,
    channel_post_handler,
)
from app.bot.handlers.sms_verify import (
    sms_hub_menu,
    service_verification_menu,
    sms_buy_callback,
    sms_cancel_callback,
    sms_view_active_callback,
)
from app.bot.handlers.whatsapp_verify import (
    whatsapp_menu,
    whatsapp_buy_callback,
    whatsapp_cancel_callback,
    whatsapp_view_active_callback,
)

logger = logging.getLogger(__name__)


async def on_startup(app) -> None:
    """Initialize DB and perform startup reconciliation before receiving traffic."""
    logger.info("Initializing database schema...")
    await init_db()

    # Instantiate services and attach to bot_data
    prodseller = ProdSellerService()
    binance = BinanceService()
    app.bot_data["prodseller_service"] = prodseller
    app.bot_data["binance_service"] = binance

    # Startup reconciliation for crash recovery
    async with AsyncSessionLocal() as session:
        order_repo = OrderRepository(session)
        checkout_repo = CheckoutRepository(session)
        audit_repo = AuditRepository(session)
        recon_service = ReconciliationService(
            order_repo, checkout_repo, audit_repo, prodseller, encryption_service
        )
        logger.info("Running startup reconciliation check for stuck orders...")
        await recon_service.reconcile_stuck_orders()
        await recon_service.reconcile_stuck_checkouts()
        await recon_service.reconcile_stuck_verifications(session)
        await session.commit()

    logger.info("Bot started successfully in production mode.")


async def on_shutdown(app) -> None:
    """Clean up open HTTP connections on shutdown."""
    logger.info("Shutting down bot...")
    prodseller: ProdSellerService = app.bot_data.get("prodseller_service")
    if prodseller:
        await prodseller.close()

    binance: BinanceService = app.bot_data.get("binance_service")
    if binance:
        await binance.close()


def create_bot_application():
    """Build and configure the Telegram application with all routing handlers."""
    request_kwargs = {
        "connect_timeout": 30.0,
        "read_timeout": 30.0,
        "write_timeout": 30.0,
        "pool_timeout": 30.0,
        "connection_pool_size": 100,
    }
    if settings.TELEGRAM_PROXY_URL:
        request_kwargs["proxy_url"] = settings.TELEGRAM_PROXY_URL

    request = HTTPXRequest(**request_kwargs)

    app = (
        ApplicationBuilder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .request(request)
        .concurrent_updates(True)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    # 1. Base Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("deposit", lambda u, c: deposit_menu_callback(u, c)))
    app.add_handler(CommandHandler(["openai", "chatgpt", "codex"], lambda u, c: service_verification_menu(u, c, "openai")))
    app.add_handler(CommandHandler("nvidia", lambda u, c: service_verification_menu(u, c, "nvidia")))
    app.add_handler(CommandHandler("youtube", lambda u, c: service_verification_menu(u, c, "youtube")))
    app.add_handler(CommandHandler("sms", sms_hub_menu))
    app.add_handler(CommandHandler(["whatsapp", "wa"], whatsapp_menu))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("balance", admin_balance_callback))
    app.add_handler(CommandHandler(["coupon", "create_coupon"], admin_coupon_command))
    app.add_handler(CommandHandler("setchannel", admin_set_channel_command))
    app.add_handler(CommandHandler("promo", promo_command))
    app.add_handler(CommandHandler("orders", my_orders_callback))
    app.add_handler(CommandHandler("support", support_callback))

    # 2. Text / Message Handlers (Persistent Menu & TxID submission)
    app.add_handler(MessageHandler(filters.Regex("^🛍 Products$"), lambda u, c: catalog_callback(u, c)))
    app.add_handler(MessageHandler(filters.Regex("^💳 Deposit$"), lambda u, c: deposit_menu_callback(u, c)))
    app.add_handler(MessageHandler(filters.Regex("^(🤖 Codex Verification|🤖 OpenAI / ChatGPT)$"), lambda u, c: service_verification_menu(u, c, "openai")))
    app.add_handler(MessageHandler(filters.Regex("^(🟢 Nvidia Verification|🟢 Nvidia|Nvidia)$"), lambda u, c: service_verification_menu(u, c, "nvidia")))
    app.add_handler(MessageHandler(filters.Regex("^📺 YouTube$"), lambda u, c: service_verification_menu(u, c, "youtube")))
    app.add_handler(MessageHandler(filters.Regex("^(📱 WhatsApp|📱 WhatsApp Verify|WhatsApp)$"), whatsapp_menu))
    app.add_handler(MessageHandler(filters.Regex("^📦 My Orders$"), lambda u, c: my_orders_callback(u, c)))
    app.add_handler(MessageHandler(filters.Regex("^🔎 Search$"), lambda u, c: search_init_callback(u, c)))
    app.add_handler(MessageHandler(filters.Regex("^🆘 Support$"), lambda u, c: support_callback(u, c)))
    app.add_handler(MessageHandler(filters.Regex("^⚙️ Admin$"), admin_command))

    # TxID, Deposit Order ID, and Search query text capture
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # 3. Callback Queries
    # Navigation
    app.add_handler(CallbackQueryHandler(home_callback, pattern=r"^nav:home$"))
    app.add_handler(CallbackQueryHandler(deposit_menu_callback, pattern=r"^nav:deposit$"))
    app.add_handler(CallbackQueryHandler(payment_guide_callback, pattern=r"^nav:guide$"))
    app.add_handler(CallbackQueryHandler(search_init_callback, pattern=r"^nav:search$"))
    app.add_handler(CallbackQueryHandler(support_callback, pattern=r"^nav:support$"))
    app.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern=r"^noop$"))

    # Deposit Flow (Binance Pay)
    app.add_handler(CallbackQueryHandler(deposit_binance_pay_callback, pattern=r"^deposit:binance_pay$"))
    app.add_handler(CallbackQueryHandler(deposit_cancel_callback, pattern=r"^deposit:cancel$"))

    # SMS Verification Suite (Codex / OpenAI / Nvidia / YouTube)
    app.add_handler(CallbackQueryHandler(sms_hub_menu, pattern=r"^sms:hub$"))
    app.add_handler(CallbackQueryHandler(lambda u, c: service_verification_menu(u, c, "openai"), pattern=r"^sms:menu:openai$"))
    app.add_handler(CallbackQueryHandler(lambda u, c: service_verification_menu(u, c, "nvidia"), pattern=r"^sms:menu:nvidia$"))
    app.add_handler(CallbackQueryHandler(lambda u, c: service_verification_menu(u, c, "youtube"), pattern=r"^sms:menu:youtube$"))
    app.add_handler(CallbackQueryHandler(sms_buy_callback, pattern=r"^sms:buy:[a-zA-Z0-9_-]+:\d+$"))
    app.add_handler(CallbackQueryHandler(sms_cancel_callback, pattern=r"^sms:cancel(:[a-zA-Z0-9_-]+)?$"))
    app.add_handler(CallbackQueryHandler(sms_view_active_callback, pattern=r"^sms:view_active$"))

    # WhatsApp OTP Verification (GrizzlySMS — USA & UK only)
    app.add_handler(CallbackQueryHandler(whatsapp_menu, pattern=r"^wa:menu$"))
    app.add_handler(CallbackQueryHandler(whatsapp_buy_callback, pattern=r"^wa:buy:\d+$"))
    app.add_handler(CallbackQueryHandler(whatsapp_cancel_callback, pattern=r"^wa:cancel(:[a-zA-Z0-9_-]+)?$"))
    app.add_handler(CallbackQueryHandler(whatsapp_view_active_callback, pattern=r"^wa:view_active$"))

    # Backward compatibility for legacy yt: callbacks
    app.add_handler(CallbackQueryHandler(lambda u, c: service_verification_menu(u, c, "youtube"), pattern=r"^yt:menu$"))
    app.add_handler(CallbackQueryHandler(sms_cancel_callback, pattern=r"^yt:cancel$"))
    app.add_handler(CallbackQueryHandler(sms_view_active_callback, pattern=r"^yt:view_active$"))

    # Catalog & Products
    app.add_handler(CallbackQueryHandler(catalog_callback, pattern=r"^catalog:\d+$"))
    app.add_handler(CallbackQueryHandler(product_detail_callback, pattern=r"^prod:[a-zA-Z0-9_-]+$"))
    app.add_handler(CallbackQueryHandler(quantity_adjust_callback, pattern=r"^qty_(inc|dec):[a-zA-Z0-9_-]+:\d+$"))

    # Checkout & Payment
    app.add_handler(CallbackQueryHandler(buy_product_callback, pattern=r"^buy:[a-zA-Z0-9_-]+:\d+$"))
    app.add_handler(CallbackQueryHandler(paid_button_callback, pattern=r"^paid:[a-zA-Z0-9_-]+$"))

    # Orders
    app.add_handler(CallbackQueryHandler(my_orders_callback, pattern=r"^orders:\d+$"))
    app.add_handler(CallbackQueryHandler(order_view_callback, pattern=r"^order_view:[a-zA-Z0-9_-]+$"))
    app.add_handler(CallbackQueryHandler(order_keys_callback, pattern=r"^order_keys:[a-zA-Z0-9_-]+$"))

    # Admin Control Panel
    app.add_handler(CallbackQueryHandler(admin_dashboard_callback, pattern=r"^admin:dashboard$"))
    app.add_handler(CallbackQueryHandler(admin_analytics_callback, pattern=r"^admin:analytics$"))
    app.add_handler(CallbackQueryHandler(admin_top_sellers_callback, pattern=r"^admin:top_sellers$"))
    app.add_handler(CallbackQueryHandler(admin_users_callback, pattern=r"^admin:users:\d+$"))
    app.add_handler(CallbackQueryHandler(admin_user_detail_callback, pattern=r"^admin:user:\d+$"))
    app.add_handler(CallbackQueryHandler(admin_toggle_block_callback, pattern=r"^admin:toggle_block:\d+$"))
    app.add_handler(CallbackQueryHandler(admin_recent_orders_callback, pattern=r"^admin:recent_orders$"))
    app.add_handler(CallbackQueryHandler(admin_coupons_callback, pattern=r"^admin:coupons$"))
    app.add_handler(CallbackQueryHandler(admin_coupon_new_service_callback, pattern=r"^admin:coupon_new_service$"))
    app.add_handler(CallbackQueryHandler(admin_coupon_set_srv_callback, pattern=r"^admin:coupon_set_srv:[a-zA-Z0-9_-]+$"))
    app.add_handler(CallbackQueryHandler(admin_coupon_delete_callback, pattern=r"^admin:coupon_delete:[a-zA-Z0-9_-]+$"))
    app.add_handler(CallbackQueryHandler(admin_balance_callback, pattern=r"^admin:balance$"))
    app.add_handler(CallbackQueryHandler(admin_refresh_cache_callback, pattern=r"^admin:refresh_cache$"))
    app.add_handler(CallbackQueryHandler(admin_problems_callback, pattern=r"^admin:problems$"))

    # Mandatory Channel Subscription Verification
    app.add_handler(CallbackQueryHandler(force_sub_verify_callback, pattern=r"^force_sub:verify$"))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, channel_post_handler))

    # Global Error Handler
    app.add_error_handler(global_error_handler)

    return app


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log any unhandled exception gracefully."""
    logger.error("Exception occurred in update handler: %s", context.error, exc_info=context.error)


async def handle_text_message(update, context):
    """Router for plain text inputs based on active conversation flags."""
    # 0. Check if admin forwarded a post from channel to auto-connect
    if await forward_channel_post_handler(update, context):
        return

    user = update.effective_user
    if user and not await is_user_channel_member(context.bot, user.id):
        await send_force_sub_prompt(update, context)
        return

    # 1. Check if user is in deposit flow
    if await handle_deposit_text_message(update, context):
        return

    # Safe user_data checks
    if context.user_data is not None:
        # 2. Check if user is submitting checkout TxID/Order ID
        if context.user_data.get("awaiting_txid_checkout_id"):
            await txid_message_handler(update, context)
        # 3. Check if user is searching catalog
        elif context.user_data.get("awaiting_search"):
            await search_query_handler(update, context)


import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"7minti Telegram Reseller Bot is Running Healthy!")

    def log_message(self, format, *args):
        return  # Suppress health check access logs


def start_health_check_server():
    port = int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()


def main():
    """Main application entrypoint."""
    setup_logging(settings.LOG_LEVEL)
    logger.info("Bootstrapping ProdSeller Telegram Reseller Bot...")

    # If running on cloud platforms with PORT (e.g. Render.com Free Web Service)
    if os.getenv("PORT"):
        logger.info("Starting background health server on port %s", os.getenv("PORT"))
        threading.Thread(target=start_health_check_server, daemon=True).start()

    app = create_bot_application()
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
