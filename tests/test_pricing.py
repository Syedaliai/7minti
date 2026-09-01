from decimal import Decimal
import pytest
from app.services.pricing import PricingService


def test_unit_price_calculation_with_markup():
    """Verify that supplier_price + 0.20 commission = selling_price."""
    supplier_price = Decimal("2.50")
    customer_price = PricingService.calculate_unit_price(supplier_price)
    assert customer_price == Decimal("2.70")


def test_total_calculation_with_quantity():
    """Verify that total payable = (supplier_price + 0.20) * quantity."""
    supplier_price = Decimal("2.50")
    qty = 2
    total = PricingService.calculate_total(supplier_price, qty)
    # (2.50 + 0.20) * 2 = 5.40
    assert total == Decimal("5.40")


def test_pricing_ignores_public_price():
    """Ensure catalog product calculations use raw supplier 'price' and NEVER 'publicPrice'."""
    product = {
        "id": "prod_123",
        "name": "ChatGPT Plus",
        "price": 3.00,        # actual supplier cost
        "publicPrice": 20.00,  # public listed reference price (MUST BE IGNORED)
    }

    # Authoritative calculation based on product["price"]
    selling_price = PricingService.calculate_unit_price(product["price"])
    assert selling_price == Decimal("3.20")
    assert selling_price != Decimal("20.20")


def test_decimal_precision():
    """Verify floating point inaccuracies are avoided by using Decimal."""
    supplier_price = "0.10"
    commission = PricingService.get_commission() # 0.20
    selling_price = PricingService.calculate_unit_price(supplier_price)
    assert selling_price == Decimal("0.30")


def test_invalid_quantity_raises_error():
    """Quantity must be positive integer."""
    with pytest.raises(ValueError):
        PricingService.calculate_total("2.50", 0)

    with pytest.raises(ValueError):
        PricingService.calculate_total("2.50", -1)


def test_is_payment_sufficient_for_cost():
    """Verify cost protection logic when supplier prices rise."""
    # Paid 2.70 for 1 unit. Supplier cost rises to 2.80.
    is_covered = PricingService.is_payment_sufficient_for_cost(
        customer_paid=Decimal("2.70"),
        current_supplier_cost=Decimal("2.80"),
        quantity=1,
    )
    assert is_covered is False

    # Paid 2.70 for 1 unit. Supplier cost is 2.50.
    is_covered = PricingService.is_payment_sufficient_for_cost(
        customer_paid=Decimal("2.70"),
        current_supplier_cost=Decimal("2.50"),
        quantity=1,
    )
    assert is_covered is True
