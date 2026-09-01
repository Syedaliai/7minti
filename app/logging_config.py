import logging
import re
import sys
from typing import List


class SensitiveDataFilter(logging.Filter):
    """Filter that masks sensitive tokens, API keys, private keys, and delivered secrets in log records."""

    PATTERNS = [
        # Mask API Keys and Secrets
        (re.compile(r"(psk_[a-zA-Z0-9_-]{8})[a-zA-Z0-9_-]+"), r"\1***REDACTED***"),
        (re.compile(r"(X-API-Key:\s*)[^\s,;]+", re.IGNORECASE), r"\1***REDACTED***"),
        (re.compile(r"(signature=[a-zA-Z0-9]{8})[a-zA-Z0-9]+", re.IGNORECASE), r"\1***REDACTED***"),
        (re.compile(r"(binance[_-]?api[_-]?(?:key|secret)[\"']?\s*[:=]\s*[\"']?)[^\s\"']+", re.IGNORECASE), r"\1***REDACTED***"),
        # Mask deliveredKey content
        (re.compile(r"('deliveredKey':\s*')[^']+(')", re.IGNORECASE), r"\1***REDACTED_CREDENTIAL***\2"),
        (re.compile(r'("deliveredKey":\s*")[^"]+(")', re.IGNORECASE), r"\1***REDACTED_CREDENTIAL***\2"),
        (re.compile(r"('deliveredKeys':\s*\[)[^\]]+(\])", re.IGNORECASE), r"\1***REDACTED_KEYS***\2"),
        (re.compile(r'("deliveredKeys":\s*\[)[^\]]+(\])', re.IGNORECASE), r"\1***REDACTED_KEYS***\2"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pattern, repl in self.PATTERNS:
                record.msg = pattern.sub(repl, record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._sanitize_value(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._sanitize_value(v) for v in record.args)
        return True

    def _sanitize_value(self, val):
        if isinstance(val, str):
            for pattern, repl in self.PATTERNS:
                val = pattern.sub(repl, val)
        return val


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structured console logging with sensitive data redaction."""
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Avoid duplicate handlers if called multiple times
    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(numeric_level)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        handler.addFilter(SensitiveDataFilter())
        root_logger.addHandler(handler)

    # Suppress verbose logs from noisy 3rd-party libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
