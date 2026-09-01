import time
from typing import Dict


class RateLimiter:
    """In-memory sliding window rate limiter for Telegram user actions."""

    def __init__(self, max_requests: int = 5, window_seconds: int = 10):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._user_timestamps: Dict[int, list] = {}

    def is_allowed(self, user_id: int) -> bool:
        """Check whether user action is within allowed request thresholds."""
        now = time.time()
        timestamps = self._user_timestamps.get(user_id, [])

        # Filter timestamps within current window
        valid_timestamps = [t for t in timestamps if now - t < self.window_seconds]

        if len(valid_timestamps) >= self.max_requests:
            self._user_timestamps[user_id] = valid_timestamps
            return False

        valid_timestamps.append(now)
        self._user_timestamps[user_id] = valid_timestamps
        return True


# Dedicated rate limiters
verify_rate_limiter = RateLimiter(max_requests=3, window_seconds=15)
general_rate_limiter = RateLimiter(max_requests=10, window_seconds=5)
