"""Initial database schema migration.

Revision ID: 001_initial
Revises:
Create Date: 2026-08-31 12:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Users Table
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column("is_blocked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_id"),
    )
    op.create_index(op.f("ix_users_telegram_id"), "users", ["telegram_id"], unique=True)

    # 2. Checkouts Table
    op.create_table(
        "checkouts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.String(length=100), nullable=False),
        sa.Column("product_name", sa.String(length=500), nullable=False),
        sa.Column("supplier_price_at_quote", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("commission", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("customer_unit_price", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("expected_total", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("coin", sa.String(length=20), nullable=False),
        sa.Column("network", sa.String(length=20), nullable=False),
        sa.Column("payment_address", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_checkouts_user_id"), "checkouts", ["user_id"], unique=False)
    op.create_index(op.f("ix_checkouts_status"), "checkouts", ["status"], unique=False)

    # 3. Payments Table
    op.create_table(
        "payments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("checkout_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("txid", sa.String(length=255), nullable=False),
        sa.Column("coin", sa.String(length=20), nullable=False),
        sa.Column("network", sa.String(length=20), nullable=False),
        sa.Column("expected_amount", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("received_amount", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("binance_status", sa.String(length=50), nullable=True),
        sa.Column("confirmations", sa.Integer(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_reference_metadata", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["checkout_id"], ["checkouts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("checkout_id"),
        sa.UniqueConstraint("txid"),
    )
    op.create_index(op.f("ix_payments_txid"), "payments", ["txid"], unique=True)
    op.create_index(op.f("ix_payments_checkout_id"), "payments", ["checkout_id"], unique=True)

    # 4. Orders Table
    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("checkout_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.String(length=100), nullable=False),
        sa.Column("supplier_order_id", sa.String(length=100), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("supplier_amount", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("customer_amount", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("commission_amount", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("delivery_type", sa.String(length=50), nullable=True),
        sa.Column("delivered_data_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["checkout_id"], ["checkouts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("checkout_id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("supplier_order_id"),
    )
    op.create_index(op.f("ix_orders_idempotency_key"), "orders", ["idempotency_key"], unique=True)
    op.create_index(op.f("ix_orders_supplier_order_id"), "orders", ["supplier_order_id"], unique=True)
    op.create_index(op.f("ix_orders_status"), "orders", ["status"], unique=False)

    # 5. Admin Audit Log Table
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("admin_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_admin_audit_logs_admin_telegram_id"), "admin_audit_logs", ["admin_telegram_id"], unique=False)


def downgrade() -> None:
    op.drop_table("admin_audit_logs")
    op.drop_table("orders")
    op.drop_table("payments")
    op.drop_table("checkouts")
    op.drop_table("users")
