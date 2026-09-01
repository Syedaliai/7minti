import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class ProdSellerAPIError(Exception):
    """Base exception for ProdSeller API errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, error_code: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class ProdSellerAuthError(ProdSellerAPIError):
    """401 Unauthorized — Invalid API Key."""
    pass


class ProdSellerBalanceError(ProdSellerAPIError):
    """402 Insufficient Supplier Balance."""
    pass


class ProdSellerOutOfStockError(ProdSellerAPIError):
    """409 Conflict — Product out of stock."""
    pass


class ProdSellerRateLimitError(ProdSellerAPIError):
    """429 Too Many Requests."""
    pass


class ProdSellerNotFoundError(ProdSellerAPIError):
    """404 Resource not found."""
    pass


class ProdSellerService:
    """Asynchronous HTTP client for interacting with the ProdSeller API."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, cache_ttl: Optional[int] = None):
        self.api_key = api_key or settings.PRODSELLER_API_KEY
        self.base_url = (base_url or settings.PRODSELLER_BASE_URL).rstrip("/")
        self.cache_ttl = cache_ttl if cache_ttl is not None else settings.PRODUCT_CACHE_TTL

        # In-memory short-lived catalog cache
        self._cached_products: Optional[List[Dict[str, Any]]] = None
        self._cache_timestamp: float = 0.0

        # Async HTTP client with timeouts
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "X-API-Key": self.api_key,
                "Content-Type": "application/json",
                "User-Agent": "ProdSeller-Telegram-Reseller-Bot/1.0",
            },
            timeout=httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=30.0),
        )

    async def close(self) -> None:
        """Close client sessions cleanly."""
        await self._client.aclose()

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        max_retries: int = 3,
        idempotent: bool = True,
    ) -> Dict[str, Any]:
        """Execute HTTP request with rate limit handling, backoff, and error mapping."""
        req_headers = dict(headers or {})
        attempt = 0
        backoff_delay = 1.0

        while attempt < max_retries:
            attempt += 1
            try:
                logger.debug("ProdSeller request %s %s (attempt %d/%d)", method, path, attempt, max_retries)
                response = await self._client.request(
                    method=method,
                    url=path,
                    json=json_data,
                    headers=req_headers,
                )

                # Handle 429 Rate Limiting
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else backoff_delay
                    logger.warning("ProdSeller 429 Rate limit hit. Backing off for %.2f seconds", delay)
                    if attempt < max_retries:
                        await asyncio.sleep(delay)
                        backoff_delay *= 2
                        continue
                    raise ProdSellerRateLimitError("ProdSeller API rate limit exceeded.", status_code=429)

                # Handle 5xx Server Errors
                if response.status_code >= 500:
                    logger.error("ProdSeller 5xx Server error %d for %s", response.status_code, path)
                    if idempotent and attempt < max_retries:
                        await asyncio.sleep(backoff_delay)
                        backoff_delay *= 2
                        continue
                    raise ProdSellerAPIError(
                        f"ProdSeller temporary server error ({response.status_code})",
                        status_code=response.status_code,
                    )

                # Handle 4xx Client Errors
                if response.status_code >= 400:
                    try:
                        err_payload = response.json()
                        err_msg = err_payload.get("error") or err_payload.get("message") or response.text
                    except Exception:
                        err_msg = response.text or f"HTTP {response.status_code}"

                    if response.status_code == 401:
                        raise ProdSellerAuthError(f"ProdSeller Auth failed: {err_msg}", status_code=401)
                    elif response.status_code == 402:
                        raise ProdSellerBalanceError(f"ProdSeller balance insufficient: {err_msg}", status_code=402)
                    elif response.status_code == 404:
                        raise ProdSellerNotFoundError(f"ProdSeller resource not found: {err_msg}", status_code=404)
                    elif response.status_code == 409:
                        raise ProdSellerOutOfStockError(f"ProdSeller product out of stock: {err_msg}", status_code=409)
                    else:
                        raise ProdSellerAPIError(f"ProdSeller error ({response.status_code}): {err_msg}", status_code=response.status_code)

                # Parse JSON
                try:
                    return response.json()
                except Exception as ex:
                    raise ProdSellerAPIError(f"Invalid JSON response from ProdSeller: {ex}")

            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as net_err:
                logger.warning("ProdSeller network timeout/error: %s (attempt %d/%d)", net_err, attempt, max_retries)
                if attempt < max_retries and idempotent:
                    await asyncio.sleep(backoff_delay)
                    backoff_delay *= 2
                    continue
                raise ProdSellerAPIError(f"Network error communicating with ProdSeller: {net_err}")

        raise ProdSellerAPIError("Max retries exceeded for ProdSeller API request.")

    async def get_products(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Fetch product list from ProdSeller GET /products with TTL caching for browsing."""
        now = time.time()
        if not force_refresh and self._cached_products and (now - self._cache_timestamp < self.cache_ttl):
            return self._cached_products

        data = await self._request_with_retry("GET", "/products", idempotent=True)
        products = data.get("products", [])
        self._cached_products = products
        self._cache_timestamp = now
        return products

    async def get_product(self, product_id: str) -> Dict[str, Any]:
        """Fetch fresh product details directly from ProdSeller GET /products/:id.

        Always bypasses cache for checkout and fulfillment to verify authoritative real-time price & stock.
        """
        if not product_id:
            raise ValueError("product_id is required")
        return await self._request_with_retry("GET", f"/products/{product_id}", idempotent=True)

    async def get_balance(self) -> Dict[str, Any]:
        """Fetch current account balance and membership tier from ProdSeller GET /balance."""
        return await self._request_with_retry("GET", "/balance", idempotent=True)

    async def create_order(self, product_id: str, quantity: int, idempotency_key: str) -> Dict[str, Any]:
        """Create order and purchase key on ProdSeller POST /orders with Idempotency-Key.

        Note: Even on network retry, the EXACT SAME Idempotency-Key is reused to prevent double charges.
        """
        if not product_id:
            raise ValueError("product_id is required")
        if quantity <= 0:
            raise ValueError("quantity must be greater than 0")
        if not idempotency_key:
            raise ValueError("idempotency_key is required")

        headers = {
            "Idempotency-Key": idempotency_key,
        }
        payload = {
            "productId": product_id,
            "quantity": quantity,
        }

        # create_order is made idempotent via Idempotency-Key
        return await self._request_with_retry(
            method="POST",
            path="/orders",
            json_data=payload,
            headers=headers,
            max_retries=3,
            idempotent=True,
        )

    async def get_orders(self, page: int = 1, limit: int = 50, status: Optional[str] = None) -> Dict[str, Any]:
        """List orders created by this API key from ProdSeller GET /orders."""
        params = f"?page={page}&limit={limit}"
        if status:
            params += f"&status={status}"
        return await self._request_with_retry("GET", f"/orders{params}", idempotent=True)

    async def get_order(self, order_id: str) -> Dict[str, Any]:
        """Fetch status and details of an order from ProdSeller GET /orders/:id."""
        if not order_id:
            raise ValueError("order_id is required")
        return await self._request_with_retry("GET", f"/orders/{order_id}", idempotent=True)
