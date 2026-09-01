"""
SMSPool.net API Service
Handles: price lookup, number purchase, OTP polling, cancellation.
"""

import logging
from typing import Optional, Union
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SMSPOOL_BASE = "https://api.smspool.net"

# Country IDs on SMSPool
COUNTRY_US = "1"
COUNTRY_UK = "2"

# Service IDs on SMSPool
SERVICE_YOUTUBE = 1227
SERVICE_OPENAI = 671
SERVICE_NVIDIA = 651

# Order status codes returned by /sms/check
STATUS_PENDING = 1
STATUS_EXPIRED = 2
STATUS_COMPLETED = 3
STATUS_RESEND = 4
STATUS_CANCELLED = 5
STATUS_REFUNDED = 6


class SMSPoolError(Exception):
    """Raised when SMSPool API returns an error."""


class SMSPoolService:
    """Thin async wrapper around the SMSPool REST API."""

    def __init__(self, api_key: Optional[str] = None):
        self._key = api_key or settings.SMSPOOL_API_KEY
        self._client = httpx.AsyncClient(timeout=20.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_balance(self) -> float:
        """Fetch current USD balance from SMSPool API."""
        try:
            resp = await self._client.post(
                f"{SMSPOOL_BASE}/request/balance",
                data={"key": self._key},
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and "balance" in data:
                    return float(data["balance"])
                if isinstance(data, (int, float, str)):
                    return float(data)
            logger.warning("SMSPool get_balance returned status %s: %s", resp.status_code, resp.text)
            return 0.0
        except Exception as exc:
            logger.error("SMSPool get_balance error: %s", exc)
            return 0.0

    # ------------------------------------------------------------------
    # Price / availability
    # ------------------------------------------------------------------

    async def get_price(self, country_id: str, service: Union[int, str]) -> Optional[float]:
        """Fetch real-time price from SMSPool for given country and service."""
        try:
            resp = await self._client.post(
                f"{SMSPOOL_BASE}/request/price",
                data={"key": self._key, "country": country_id, "service": str(service)},
            )
            if resp.status_code != 200:
                logger.warning("SMSPool price check returned %s: %s", resp.status_code, resp.text)
                return None
            data = resp.json()
            if isinstance(data, dict):
                price_str = data.get("price")
                if price_str is not None:
                    return float(price_str)
            return None
        except Exception as exc:
            logger.error("SMSPool get_price error for country %s, service %s: %s", country_id, service, exc)
            return None

    async def get_prices_both_countries(self, service: Union[int, str]) -> dict:
        """
        Fetch live prices for US and UK.
        Returns: {"us": float|None, "uk": float|None}
        """
        us_price = await self.get_price(COUNTRY_US, service)
        uk_price = await self.get_price(COUNTRY_UK, service)
        return {"us": us_price, "uk": uk_price}

    # ------------------------------------------------------------------
    # Purchase
    # ------------------------------------------------------------------

    async def purchase_number(self, country_id: str, service: Union[int, str]) -> dict:
        """
        Purchase an SMS number.
        Returns: {"number": str, "order_id": str, "expires_in": int, "price": float}
        Raises SMSPoolError on failure.
        """
        resp = await self._client.post(
            f"{SMSPOOL_BASE}/purchase/sms",
            data={"key": self._key, "country": country_id, "service": str(service)},
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            msg = data.get("message", "Unknown error from SMSPool")
            logger.error("SMSPool purchase failed: %s | response: %s", msg, data)
            raise SMSPoolError(msg)

        return {
            "number": str(data["number"]),
            "order_id": str(data["order_id"]),
            "expires_in": int(data.get("expires_in", 599)),
            "price": float(data.get("price", 0)),
        }

    # ------------------------------------------------------------------
    # Check OTP
    # ------------------------------------------------------------------

    async def check_sms(self, order_id: str) -> dict:
        """
        Poll status of an SMS order.
        Returns: {"status": int, "sms": str|None, "full_sms": str|None}
        Status codes: 1=Pending, 2=Expired, 3=Completed, 4=Resend, 5=Cancelled
        """
        resp = await self._client.post(
            f"{SMSPOOL_BASE}/sms/check",
            data={"key": self._key, "orderid": order_id},
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "status": int(data.get("status", STATUS_PENDING)),
            "sms": data.get("sms") or None,
            "full_sms": data.get("full_sms") or None,
        }

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an active order. Returns True on success."""
        try:
            resp = await self._client.post(
                f"{SMSPOOL_BASE}/sms/cancel",
                data={"key": self._key, "orderid": order_id},
            )
            resp.raise_for_status()
            data = resp.json()
            return bool(data.get("success"))
        except Exception as exc:
            logger.error("SMSPool cancel_order error: %s", exc)
            return False
