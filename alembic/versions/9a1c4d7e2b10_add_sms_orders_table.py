"""add sms_orders table

Revision ID: 9a1c4d7e2b10
Revises: 32c884af05b2
Create Date: 2026-09-01 11:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "9a1c4d7e2b10"
down_revision: Union[str, None] = "32c884af05b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sms_orders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("smspool_order_id", sa.String(length=100), nullable=False),
        sa.Column("phone_number", sa.String(length=30), nullable=False),
        sa.Column("country", sa.String(length=10), nullable=False),
        sa.Column("service", sa.String(length=50), nullable=False),
        sa.Column("supplier_price", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("charged_price", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("otp_received", sa.String(length=50), nullable=True),
        sa.Column("full_sms", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sms_orders_smspool_order_id"), "sms_orders", ["smspool_order_id"], unique=True)
    op.create_index(op.f("ix_sms_orders_status"), "sms_orders", ["status"], unique=False)
    op.create_index(op.f("ix_sms_orders_telegram_id"), "sms_orders", ["telegram_id"], unique=False)
    op.create_index(op.f("ix_sms_orders_user_id"), "sms_orders", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_sms_orders_user_id"), table_name="sms_orders")
    op.drop_index(op.f("ix_sms_orders_telegram_id"), table_name="sms_orders")
    op.drop_index(op.f("ix_sms_orders_status"), table_name="sms_orders")
    op.drop_index(op.f("ix_sms_orders_smspool_order_id"), table_name="sms_orders")
    op.drop_table("sms_orders")