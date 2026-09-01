from decimal import Decimal, ROUND_HALF_UP
from typing import Union, Dict, Any

from app.config import settings
from app.utils.money import to_decimal


class PricingService:
    """Centralized Pricing Service — Single source of truth for all selling price calculations."""

    @staticmethod
    def get_commission() -> Decimal:
        """Return the configured per-unit profit commission."""
        return settings.PAYMENT_COMMISSION

    @classmethod
    def calculate_unit_price(cls, supplier_price: Union[str, int, float, Decimal]) -> Decimal:
        """Calculate unit selling price: CURRENT_PRODSELLER_PRICE + COMMISSION.

        Note: Uses 'price' field from supplier API. NEVER uses 'publicPrice'.
        """
        supplier_dec = to_decimal(supplier_price)
        commission = cls.get_commission()
        selling_price = supplier_dec + commission
        return selling_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @classmethod
    def calculate_total(
        cls,
        supplier_price: Union[str, int, float, Decimal],
        quantity: int,
    ) -> Decimal:
        """Calculate total customer payable: (CURRENT_SUPPLIER_PRICE + COMMISSION) * QUANTITY."""
        if quantity <= 0:
            raise ValueError("Quantity must be greater than zero.")
        unit_price = cls.calculate_unit_price(supplier_price)
        total = unit_price * Decimal(quantity)
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @classmethod
    def calculate_commission_total(cls, quantity: int) -> Decimal:
        """Calculate total profit commission for the order: COMMISSION * QUANTITY."""
        commission = cls.get_commission() * Decimal(quantity)
        return commission.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @classmethod
    def calculate_supplier_total(
        cls,
        supplier_price: Union[str, int, float, Decimal],
        quantity: int,
    ) -> Decimal:
        """Calculate supplier raw cost total: SUPPLIER_PRICE * QUANTITY."""
        supplier_dec = to_decimal(supplier_price)
        total = supplier_dec * Decimal(quantity)
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @classmethod
    def is_payment_sufficient_for_cost(
        cls,
        customer_paid: Decimal,
        current_supplier_cost: Decimal,
        quantity: int,
        commission: Decimal = Decimal("0.00"),
    ) -> bool:
        """Check if customer's verified payment covers current supplier cost + required minimum commission.

        Prevents fulfilling orders at a loss if supplier prices fluctuate upward.
        """
        required_cost = (to_decimal(current_supplier_cost) * Decimal(quantity)) + (commission * Decimal(quantity))
        return to_decimal(customer_paid) >= required_cost
