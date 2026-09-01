import hashlib
import hmac
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class BinanceAPIError(Exception):
    """Base exception for Binance API errors."""
    pass


class BinanceService:
    """Read-only Binance API service for verifying customer deposit history.

    SECURITY:
    - Minimum privilege: Only requires deposit history read permissions.
    - NEVER requests or performs withdrawals or automated fund movements.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.api_key = api_key or settings.BINANCE_API_KEY
        self.api_secret = api_secret or settings.BINANCE_API_SECRET
        self.base_url = (base_url or settings.BINANCE_BASE_URL).rstrip("/")

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "X-MBX-APIKEY": self.api_key,
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "ProdSeller-Telegram-Reseller-Bot/1.0",
            },
            timeout=httpx.Timeout(connect=8.0, read=15.0, write=8.0, pool=20.0),
        )

    async def close(self) -> None:
        """Close client sessions cleanly."""
        await self._client.aclose()

    def _sign_params(self, params: Dict[str, Any]) -> str:
        """Generate HMAC-SHA256 signature for Binance query parameters."""
        params["timestamp"] = int(time.time() * 1000)
        query_string = urlencode(sorted(params.items()))
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{query_string}&signature={signature}"

    async def get_deposit_history(
        self,
        coin: Optional[str] = None,
        status: Optional[int] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Fetch deposit history records via Binance SAPI GET /sapi/v1/capital/deposit/hisrec."""
        params: Dict[str, Any] = {
            "offset": offset,
            "limit": limit,
        }
        if coin:
            params["coin"] = coin.upper()
        if status is not None:
            params["status"] = status
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        signed_query = self._sign_params(params)
        path = f"/sapi/v1/capital/deposit/hisrec?{signed_query}"

        try:
            logger.debug("Querying Binance deposit history (coin: %s, limit: %d)", coin, limit)
            response = await self._client.get(path)

            if response.status_code != 200:
                logger.error("Binance API error %d: %s", response.status_code, response.text)
                raise BinanceAPIError(f"Binance API returned HTTP {response.status_code}: {response.text}")

            data = response.json()
            if isinstance(data, list):
                return data
            elif isinstance(data, dict) and "msg" in data:
                raise BinanceAPIError(f"Binance error: {data.get('msg')}")
            return []
        except httpx.RequestError as req_err:
            logger.error("Network error connecting to Binance API: %s", req_err)
            raise BinanceAPIError(f"Binance connection failure: {req_err}")

    async def get_pay_transactions(
        self,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Fetch Binance Pay transaction records via Binance SAPI GET /sapi/v1/pay/transactions."""
        params: Dict[str, Any] = {
            "limit": min(limit, 100),
        }
        if start_time:
            params["startTimestamp"] = start_time
        if end_time:
            params["endTimestamp"] = end_time

        signed_query = self._sign_params(params)
        path = f"/sapi/v1/pay/transactions?{signed_query}"

        try:
            logger.debug("Querying Binance Pay transactions (limit: %d)", limit)
            response = await self._client.get(path)

            if response.status_code != 200:
                logger.error("Binance Pay API error %d: %s", response.status_code, response.text)
                raise BinanceAPIError(f"Binance Pay API returned HTTP {response.status_code}: {response.text}")

            data = response.json()
            if isinstance(data, dict):
                if data.get("code") in ("000000", "0", 0) or data.get("success") is True:
                    return data.get("data", [])
                elif "msg" in data:
                    raise BinanceAPIError(f"Binance Pay error: {data.get('msg')}")
                elif "message" in data:
                    raise BinanceAPIError(f"Binance Pay error: {data.get('message')}")
            elif isinstance(data, list):
                return data
            return []
        except httpx.RequestError as req_err:
            logger.error("Network error connecting to Binance Pay API: %s", req_err)
            raise BinanceAPIError(f"Binance Pay connection failure: {req_err}")
