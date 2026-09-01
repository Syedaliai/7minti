import enum
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import relationship, Mapped, mapped_column

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CheckoutStatus(str, enum.Enum):
    CREATED = "CREATED"
    AWAITING_PAYMENT = "AWAITING_PAYMENT"
    VERIFYING = "VERIFYING"
    PAID = "PAID"
    UNDERPAID = "UNDERPAID"
    OVERPAID_REVIEW = "OVERPAID_REVIEW"
    INVALID_TXID = "INVALID_TXID"
    PRICE_CHANGED_REVIEW = "PRICE_CHANGED_REVIEW"
    PAYMENT_REVIEW = "PAYMENT_REVIEW"
    EXPIRED = "EXPIRED"


class OrderStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    PROCESSING = "PROCESSING"
    DELIVERED = "DELIVERED"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    SUPPLIER_BALANCE_LOW = "SUPPLIER_BALANCE_LOW"
    PRICE_CHANGED_REVIEW = "PRICE_CHANGED_REVIEW"
    SUPPLIER_FAILED = "SUPPLIER_FAILED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class SmsOrderStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"        # Number issued, waiting for OTP
    COMPLETED = "COMPLETED"  # OTP received
    EXPIRED = "EXPIRED"      # Timed out
    CANCELLED = "CANCELLED"  # User cancelled
    REFUNDED = "REFUNDED"    # Refunded to user balance


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0.0"), server_default="0.0", nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    checkouts: Mapped[list["Checkout"]] = relationship("Checkout", back_populates="user", cascade="all, delete-orphan")
    payments: Mapped[list["Payment"]] = relationship("Payment", back_populates="user", cascade="all, delete-orphan")
    orders: Mapped[list["Order"]] = relationship("Order", back_populates="user", cascade="all, delete-orphan")


class Checkout(Base):
    __tablename__ = "checkouts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    product_name: Mapped[str] = mapped_column(String(500), nullable=False)
    supplier_price_at_quote: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    customer_unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, CheckConstraint("quantity > 0"), nullable=False, default=1)
    expected_total: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    coin: Mapped[str] = mapped_column(String(20), nullable=False)
    network: Mapped[str] = mapped_column(String(20), nullable=False)
    payment_address: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default=CheckoutStatus.AWAITING_PAYMENT.value, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="checkouts")
    payment: Mapped[Optional["Payment"]] = relationship("Payment", back_populates="checkout", uselist=False, cascade="all, delete-orphan")
    order: Mapped[Optional["Order"]] = relationship("Order", back_populates="checkout", uselist=False, cascade="all, delete-orphan")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    checkout_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("checkouts.id"), nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    txid: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    coin: Mapped[str] = mapped_column(String(20), nullable=False)
    network: Mapped[str] = mapped_column(String(20), nullable=False)
    expected_amount: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    received_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    binance_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    confirmations: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_reference_metadata: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    checkout: Mapped[Optional["Checkout"]] = relationship("Checkout", back_populates="payment")
    user: Mapped["User"] = relationship("User", back_populates="payments")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    checkout_id: Mapped[str] = mapped_column(String(36), ForeignKey("checkouts.id"), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    supplier_order_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    supplier_amount: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    customer_amount: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    commission_amount: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(30), default=OrderStatus.NOT_STARTED.value, nullable=False, index=True)
    delivery_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    delivered_data_encrypted: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    checkout: Mapped["Checkout"] = relationship("Checkout", back_populates="order")
    user: Mapped["User"] = relationship("User", back_populates="orders")


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class SmsOrder(Base):
    """Tracks every SMSPool number purchase for YouTube Channel Verification."""
    __tablename__ = "sms_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    smspool_order_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    phone_number: Mapped[str] = mapped_column(String(30), nullable=False)
    country: Mapped[str] = mapped_column(String(10), nullable=False)  # "us" or "uk"
    service: Mapped[str] = mapped_column(String(50), nullable=False, default="YouTube")
    supplier_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)  # Raw SMSPool price
    charged_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)   # After 60% commission
    status: Mapped[str] = mapped_column(String(20), default=SmsOrderStatus.ACTIVE.value, nullable=False, index=True)
    otp_received: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    full_sms: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User")


class WhatsAppOrderStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"        # Number issued, waiting for OTP
    COMPLETED = "COMPLETED"  # OTP received successfully
    EXPIRED = "EXPIRED"      # Polling timed out without OTP
    CANCELLED = "CANCELLED"  # User-cancelled or supplier-cancelled
    REFUNDED = "REFUNDED"    # Auto-refunded back to balance


class WhatsAppOrder(Base):
    """
    Tracks every GrizzlySMS WhatsApp number purchase.
    Stores activation_id (supplier reference), phone number, raw cost,
    and customer-charged price (with 80% commission).
    """
    __tablename__ = "whatsapp_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    grizzly_activation_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    phone_number: Mapped[str] = mapped_column(String(30), nullable=False)
    country: Mapped[str] = mapped_column(String(10), nullable=False)   # "us" or "uk"
    supplier_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)   # Raw GrizzlySMS price
    charged_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)    # After 80% commission
    status: Mapped[str] = mapped_column(String(20), default=WhatsAppOrderStatus.ACTIVE.value, nullable=False, index=True)
    otp_received: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    full_sms: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User")


class CouponDiscountType(str, enum.Enum):
    PERCENTAGE = "PERCENTAGE"  # e.g., 20 = 20% off
    FIXED = "FIXED"            # e.g., 0.10 = $0.10 USDT off


class Coupon(Base):
    """
    Coupons generated by admin with strict service-scoping.
    service can be: 'all', 'whatsapp', 'openai', 'nvidia', 'youtube', 'products'
    """
    __tablename__ = "coupons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    service: Mapped[str] = mapped_column(String(30), default="all", nullable=False, index=True)
    discount_type: Mapped[str] = mapped_column(String(20), default=CouponDiscountType.PERCENTAGE.value, nullable=False)
    discount_value: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    times_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class CouponRedemption(Base):
    """
    Tracks each time a user redeems a coupon to prevent duplicate reuse.
    """
    __tablename__ = "coupon_redemptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    coupon_id: Mapped[str] = mapped_column(String(36), ForeignKey("coupons.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    discount_applied: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    service: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    coupon: Mapped["Coupon"] = relationship("Coupon")
    user: Mapped["User"] = relationship("User")
