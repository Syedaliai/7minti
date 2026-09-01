import pytest
import respx
import httpx
from app.services.prodseller import (
    ProdSellerService,
    ProdSellerAuthError,
    ProdSellerBalanceError,
    ProdSellerOutOfStockError,
    ProdSellerRateLimitError,
    ProdSellerAPIError,
)


@pytest.mark.asyncio
@respx.mock
async def test_prodseller_get_products_caching():
    """Verify catalog caching returns cached items on repeated call within TTL."""
    respx.get("https://prodseller.com/v1/products").respond(
        200,
        json={"products": [{"id": "p1", "name": "Item 1", "price": 2.50, "inStock": True}]},
    )

    service = ProdSellerService(cache_ttl=60)
    prods1 = await service.get_products()
    assert len(prods1) == 1
    assert prods1[0]["name"] == "Item 1"

    # Second call should use cache without hitting network again
    prods2 = await service.get_products()
    assert prods2 == prods1

    await service.close()


@pytest.mark.asyncio
@respx.mock
async def test_prodseller_401_auth_error():
    """Verify 401 raises ProdSellerAuthError."""
    respx.get("https://prodseller.com/v1/balance").respond(
        401,
        json={"error": "API key invalid"},
    )
    service = ProdSellerService()
    with pytest.raises(ProdSellerAuthError):
        await service.get_balance()
    await service.close()


@pytest.mark.asyncio
@respx.mock
async def test_prodseller_402_insufficient_balance_error():
    """Verify 402 raises ProdSellerBalanceError."""
    respx.post("https://prodseller.com/v1/orders").respond(
        402,
        json={"error": "Solde insuffisant"},
    )
    service = ProdSellerService()
    with pytest.raises(ProdSellerBalanceError):
        await service.create_order(product_id="p1", quantity=1, idempotency_key="key1")
    await service.close()


@pytest.mark.asyncio
@respx.mock
async def test_prodseller_409_out_of_stock_error():
    """Verify 409 raises ProdSellerOutOfStockError."""
    respx.post("https://prodseller.com/v1/orders").respond(
        409,
        json={"error": "Out of stock"},
    )
    service = ProdSellerService()
    with pytest.raises(ProdSellerOutOfStockError):
        await service.create_order(product_id="p1", quantity=1, idempotency_key="key1")
    await service.close()


@pytest.mark.asyncio
@respx.mock
async def test_prodseller_429_rate_limit_backoff():
    """Verify 429 response is handled with retry."""
    # First returns 429 with retry header, second returns 200
    route = respx.get("https://prodseller.com/v1/products/p1")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0.1"}, json={"error": "Rate limit"}),
        httpx.Response(200, json={"id": "p1", "name": "Item 1", "price": 2.50, "inStock": True}),
    ]

    service = ProdSellerService()
    prod = await service.get_product("p1")
    assert prod["id"] == "p1"
    assert prod["price"] == 2.50
    await service.close()
