import html
import re


def escape_html(text: str) -> str:
    """Safely escape text for Telegram HTML parse mode."""
    if not text:
        return ""
    return html.escape(str(text))


def clean_supplier_text(text: str) -> str:
    """Sanitize description text from supplier API for safe presentation in HTML."""
    if not text:
        return ""
    # Strip HTML tags if supplier sent raw HTML, or escape properly
    cleaned = re.sub(r"<[^>]*>", "", text)
    return html.escape(cleaned.strip())
