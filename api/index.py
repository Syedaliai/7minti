import json
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
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
            </style>
        </head>
        <body>
            <div class="card">
                <div class="badge">⚡ LIVE & READY</div>
                <h1>7 Minti Bot Engine</h1>
                <p>Enterprise Telegram Digital Store, GrizzlySMS, SMSPool & Binance Pay Auto-Deposit Engine.</p>
                <p>Database: <strong>Neon PostgreSQL Cloud</strong></p>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html.encode("utf-8"))
        return

    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "status": "processed"}).encode("utf-8"))
        return
