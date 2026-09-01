import asyncio
import json
import logging
from http.server import BaseHTTPRequestHandler
from telegram import Update
from app.main import create_bot_application
from app.config import settings

logger = logging.getLogger(__name__)

# Global Application instance cached across lambda invocations
_app = None


def get_app():
    global _app
    if _app is None:
        _app = create_bot_application()
    return _app


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Endpoint to auto-register Telegram Webhook
        if "set_webhook" in self.path:
            async def set_hook():
                app = get_app()
                async with app:
                    webhook_url = "https://7minti.vercel.app/"
                    await app.bot.set_webhook(url=webhook_url)
                    info = await app.bot.get_webhook_info()
                    return info.url

            try:
                url = asyncio.run(set_hook())
                msg = f"Webhook successfully registered to: {url}"
            except Exception as e:
                msg = f"Failed to set webhook: {e}"

            self.send_response(200)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(msg.encode("utf-8"))
            return

        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>7 Minti Telegram Bot</title>
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                .card { background: #1e293b; padding: 2.5rem; border-radius: 1rem; box-shadow: 0 10px 25px rgba(0,0,0,0.5); text-align: center; max-width: 480px; border: 1px solid #334155; }
                h1 { color: #38bdf8; margin-top: 0; }
                .badge { background: #059669; color: #fff; padding: 0.35rem 0.85rem; border-radius: 9999px; font-weight: 600; font-size: 0.875rem; display: inline-block; margin-bottom: 1rem; }
                p { color: #94a3b8; line-height: 1.6; }
                .btn { display: inline-block; background: #3b82f6; color: #fff; padding: 0.75rem 1.5rem; border-radius: 0.5rem; text-decoration: none; font-weight: 600; margin-top: 1rem; transition: background 0.2s; }
                .btn:hover { background: #2563eb; }
            </style>
        </head>
        <body>
            <div class="card">
                <div class="badge">⚡ LIVE & READY</div>
                <h1>7 Minti Bot Engine</h1>
                <p>Enterprise Telegram Digital Store, GrizzlySMS, SMSPool & Binance Pay Auto-Deposit Engine.</p>
                <p>Database: <strong>Neon PostgreSQL Cloud</strong></p>
                <a href="/set_webhook" class="btn">🔗 Click Here to Connect Telegram Webhook</a>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html.encode("utf-8"))
        return

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_response(200)
            self.end_headers()
            return

        post_data = self.rfile.read(content_length)

        async def process():
            try:
                data = json.loads(post_data.decode("utf-8"))
                app = get_app()
                async with app:
                    update = Update.de_json(data, app.bot)
                    if update:
                        await app.process_update(update)
            except Exception as e:
                logger.error("Error processing webhook update: %s", e)

        try:
            asyncio.run(process())
        except Exception as err:
            logger.error("Asyncio execution error: %s", err)

        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
        return
