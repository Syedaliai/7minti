"""
GrizzlySMS API Service — WhatsApp OTP Numbers (USA 🇺🇸 & UK 🇬🇧 only)

API base:  https://api.grizzlysms.com/stubs/handler_api.php
API style: SMS-Activate-compatible (query-string based, plain-text responses)

All prices from the API are in USD (float). The 80% commission markup is
applied server-side by the handler — this module only returns raw prices.

Architecture mirrors app/services/prodseller.py for consistency:
  - Async httpx client with timeouts
  - Retry with exponential backoff on 5xx / network errors
  - Custom exception hierarchy
  - All methods are coroutines (non-blocking)
"""

import asyncio
import logging
from decimal import Decimal
from typing import Dict, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# ─── GrizzlySMS Country Codes ─────────────────────────────────────────────────
# Found from grizzlysms.com/price page — WhatsApp service, US=12 / UK=16
# These are the integer IDs used in the ?country= parameter.
GRIZZLY_COUNTRY_US = "12"   # United States
GRIZZLY_COUNTRY_UK = "16"   # United Kingdom

# Human-readable map for display
GRIZZLY_COUNTRY_NAMES: Dict[str, str] = {
    GRIZZLY_COUNTRY_US: "United States 🇺🇸",
    GRIZZLY_COUNTRY_UK: "United Kingdom 🇬🇧",
}

# ─── GrizzlySMS Service Code ───────────────────────────────────────────────────
# WhatsApp service code on GrizzlySMS is "wa"
GRIZZLY_SERVICE_WHATSAPP = "wa"

# ─── Activation Status Codes ──────────────────────────────────────────────────
# Returned by action=getStatus
STATUS_WAITING_CODE = 1      # Number issued, waiting for SMS
STATUS_SMS_RECEIVED = 2      # OTP/SMS received — value in response
STATUS_CANCELLED = 3         # Activation cancelled by user
STATUS_CODE_CANCELLED = 6    # Cancelled after OTP received (SMS resend)
STATUS_FINISHED = 8          # Activation successfully completed

# ─── API Base URL ─────────────────────────────────────────────────────────────
GRIZZLY_API_BASE = "https://api.grizzlysms.com/stubs/handler_api.php"


# ─── Exceptions ───────────────────────────────────────────────────────────────

class GrizzlySMSError(Exception):
    """Base exception for GrizzlySMS API errors."""


class GrizzlySMSAuthError(GrizzlySMSError):
    """BAD_KEY — Invalid API key."""


class GrizzlySMSBalanceError(GrizzlySMSError):
    """NO_BALANCE — Insufficient supplier balance."""


class GrizzlySMSNoNumbersError(GrizzlySMSError):
    """NO_NUMBERS — No numbers available for the requested country/service."""


class GrizzlySMSRateLimitError(GrizzlySMSError):
    """Too many requests to GrizzlySMS API."""


class GrizzlySMSNetworkError(GrizzlySMSError):
    """Network-level error communicating with GrizzlySMS."""


# ─── Service ──────────────────────────────────────────────────────────────────

class GrizzlySMSService:
    """
    Async client for GrizzlySMS API.
    Scoped strictly to WhatsApp service, USA and UK countries only.
    """

    def __init__(self, api_key: Optional[str] = None):
        self._key = api_key or settings.GRIZZLYSMS_API_KEY
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=30.0),
            headers={"User-Agent": "GrizzlySMS-TelegramBot/1.0"},
        )

    async def close(self) -> None:
        """Close underlying HTTP connection pool."""
        await self._client.aclose()

    # ── Internal request helper ────────────────────────────────────────────────

    async def _get(
        self,
        params: Dict[str, str],
        max_retries: int = 3,
    ) -> str:
        """
        Execute a GET request with exponential-backoff retry on 5xx / network errors.
        GrizzlySMS returns plain-text responses (not JSON).
        Returns the raw response text, stripped.
        """
        params["api_key"] = self._key
        backoff = 1.0

        for attempt in range(1, max_retries + 1):
            try:
                logger.debug("GrizzlySMS request (attempt %d/%d): params=%s", attempt, max_retries, {k: v for k, v in params.items() if k != "api_key"})
                resp = await self._client.get(GRIZZLY_API_BASE, params=params)

                if resp.status_code == 429:
                    if attempt < max_retries:
                        logger.warning("GrizzlySMS 429 rate limit. Backing off %.1fs", backoff)
                        await asyncio.sleep(backoff)
                        backoff *= 2
                        continue
                    raise GrizzlySMSRateLimitError("GrizzlySMS rate limit exceeded.")

                if resp.status_code >= 500:
                    if attempt < max_retries:
                        logger.warning("GrizzlySMS 5xx error (%d). Backing off %.1fs", resp.status_code, backoff)
                        await asyncio.sleep(backoff)
                        backoff *= 2
                        continue
                    raise GrizzlySMSError(f"GrizzlySMS server error: HTTP {resp.status_code}")

                text = resp.text.strip()
                logger.debug("GrizzlySMS response: %s", text)
                return text

            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as net_err:
                if attempt < max_retries:
                    logger.warning("GrizzlySMS network error: %s. Backing off %.1fs", net_err, backoff)
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                raise GrizzlySMSNetworkError(f"Network error with GrizzlySMS: {net_err}") from net_err

        raise GrizzlySMSError("Max retries exceeded for GrizzlySMS API.")

    def _check_error(self, text: str) -> None:
        """Raise appropriate exception for known GrizzlySMS error strings."""
        if text == "BAD_KEY":
            raise GrizzlySMSAuthError("GrizzlySMS: Invalid API key (BAD_KEY).")
        if text == "NO_BALANCE":
            raise GrizzlySMSBalanceError("GrizzlySMS: Insufficient balance (NO_BALANCE).")
        if text == "NO_NUMBERS":
            raise GrizzlySMSNoNumbersError("GrizzlySMS: No numbers available (NO_NUMBERS). Try another country.")
        if text.startswith("BAD_"):
            raise GrizzlySMSError(f"GrizzlySMS API error: {text}")

    # ── Public Methods ─────────────────────────────────────────────────────────

    async def get_whatsapp_price(self, country_id: str) -> Optional[Decimal]:
        """
        Fetch real-time price for WhatsApp numbers in a specific country.

        Uses action=getPrices (returns JSON keyed by service code, then country code).
        Returns Decimal price in USD, or None if not available.

        Security: price is fetched from server and NEVER accepted from client-side.
        """
        if country_id not in (GRIZZLY_COUNTRY_US, GRIZZLY_COUNTRY_UK):
            raise ValueError(f"Country {country_id!r} is not supported. Only US ({GRIZZLY_COUNTRY_US}) and UK ({GRIZZLY_COUNTRY_UK}) are allowed.")

        try:
            text = await self._get({
                "action": "getPrices",
                "service": GRIZZLY_SERVICE_WHATSAPP,
                "country": country_id,
            })
            import json
            data = json.loads(text)
            # Grizzly returns either {country_id: {"wa": {"cost": 0.15, "count": 100}}}
            # or {"wa": {country_id: {"cost": 0.15, "count": 100}}}
            cost = None
            count = 0
            if str(country_id) in data and isinstance(data[str(country_id)], dict):
                wa_data = data[str(country_id)].get(GRIZZLY_SERVICE_WHATSAPP, {})
                cost = wa_data.get("cost")
                count = int(wa_data.get("count", 0))
            elif GRIZZLY_SERVICE_WHATSAPP in data and isinstance(data[GRIZZLY_SERVICE_WHATSAPP], dict):
                c_data = data[GRIZZLY_SERVICE_WHATSAPP].get(str(country_id), {})
                cost = c_data.get("cost")
                count = int(c_data.get("count", 0))

            if cost is not None and count > 0:
                return Decimal(str(cost))
            return None
        except (GrizzlySMSAuthError, GrizzlySMSBalanceError, GrizzlySMSRateLimitError, GrizzlySMSNetworkError):
            raise
        except Exception as exc:
            logger.error("GrizzlySMS get_whatsapp_price error (country=%s): %s", country_id, exc)
            return None

    async def get_prices_both_countries(self) -> Dict[str, Optional[Decimal]]:
        """
        Fetch live WhatsApp prices for both USA and UK in parallel.
        Returns: {"us": Decimal|None, "uk": Decimal|None}
        """
        us_price, uk_price = await asyncio.gather(
            self.get_whatsapp_price(GRIZZLY_COUNTRY_US),
            self.get_whatsapp_price(GRIZZLY_COUNTRY_UK),
            return_exceptions=True,
        )
        # Convert exceptions to None so the menu gracefully shows "Unavailable"
        return {
            "us": us_price if isinstance(us_price, Decimal) else None,
            "uk": uk_price if isinstance(uk_price, Decimal) else None,
        }

    async def request_number(
        self,
        country_id: str,
        max_price: Optional[Decimal] = None,
    ) -> Dict[str, str]:
        """
        Purchase a WhatsApp number from GrizzlySMS.

        Returns:
            {
                "activation_id": str,  # Used for polling and cancellation
                "phone_number": str,   # The actual phone number (without leading +)
            }

        Raises:
            GrizzlySMSAuthError, GrizzlySMSBalanceError, GrizzlySMSNoNumbersError,
            GrizzlySMSError on any failure.

        Security: country_id is validated server-side against whitelist. maxPrice
        is set server-side based on freshly fetched price — never trusting user input.
        """
        if country_id not in (GRIZZLY_COUNTRY_US, GRIZZLY_COUNTRY_UK):
            raise ValueError(f"Country {country_id!r} not in allowed list [US, UK].")

        params: Dict[str, str] = {
            "action": "getNumber",
            "service": GRIZZLY_SERVICE_WHATSAPP,
            "country": country_id,
        }
        if max_price is not None:
            # Set maxPrice to the fetched cost to avoid purchasing above our budget
            params["maxPrice"] = str(max_price)

        text = await self._get(params)
        self._check_error(text)

        # Successful response format: "ACCESS_NUMBER:<activation_id>:<phone_number>"
        if not text.startswith("ACCESS_NUMBER:"):
            raise GrizzlySMSError(f"Unexpected GrizzlySMS getNumber response: {text!r}")

        parts = text.split(":")
        if len(parts) != 3:
            raise GrizzlySMSError(f"Malformed ACCESS_NUMBER response: {text!r}")

        return {
            "activation_id": parts[1].strip(),
            "phone_number": parts[2].strip(),
        }

    async def get_activation_status(self, activation_id: str) -> Dict[str, object]:
        """
        Poll the status of an active activation.

        Returns:
            {
                "status": int,         # STATUS_* constant above
                "otp": str | None,     # The OTP code if received
                "full_sms": str | None # Full SMS text if received
            }

        Response format: "STATUS_WAIT_CODE" | "STATUS_SMS_RECEIVED:<code>" | etc.
        """
        text = await self._get({
            "action": "getStatus",
            "id": activation_id,
        })
        self._check_error(text)

        if text == "STATUS_WAIT_CODE":
            return {"status": STATUS_WAITING_CODE, "otp": None, "full_sms": None}

        if text.startswith("STATUS_OK:") or text.startswith("STATUS_SMS_RECEIVED:"):
            # Extract OTP from "STATUS_OK:<otp>" or "STATUS_SMS_RECEIVED:<otp>"
            otp = text.split(":", 1)[1].strip()
            return {"status": STATUS_SMS_RECEIVED, "otp": otp, "full_sms": otp}

        if text == "STATUS_CANCEL":
            return {"status": STATUS_CANCELLED, "otp": None, "full_sms": None}

        if text == "STATUS_FINISH":
            return {"status": STATUS_FINISHED, "otp": None, "full_sms": None}

        logger.warning("GrizzlySMS unknown getStatus response: %s", text)
        return {"status": STATUS_WAITING_CODE, "otp": None, "full_sms": None}

    async def cancel_activation(self, activation_id: str) -> bool:
        """
        Cancel an active activation (setStatus=8 = cancel).
        Returns True on success, False on failure.
        """
        try:
            text = await self._get({
                "action": "setStatus",
                "status": "8",
                "id": activation_id,
            })
            # Expected: "ACCESS_CANCEL" on success
            return text == "ACCESS_CANCEL"
        except Exception as exc:
            logger.error("GrizzlySMS cancel_activation(%s) error: %s", activation_id, exc)
            return False

    async def get_balance(self) -> Optional[Decimal]:
        """
        Check the GrizzlySMS account balance.
        Returns Decimal balance in USD, or None on error.
        """
        try:
            text = await self._get({"action": "getBalance"})
            self._check_error(text)
            # Expected: "ACCESS_BALANCE:<amount>"
            if text.startswith("ACCESS_BALANCE:"):
                amount = text.split(":", 1)[1].strip()
                return Decimal(amount)
            return None
        except Exception as exc:
            logger.error("GrizzlySMS get_balance error: %s", exc)
            return None
