"""add balance to users and update payment nullable checkout_id

Revision ID: 65899730c03e
Revises: cb5edb100649
Create Date: 2026-09-01 09:47:09.180938

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '65899730c03e'
down_revision: Union[str, None] = 'cb5edb100649'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use batch mode for SQLite compatibility
    with op.batch_alter_table('payments') as batch_op:
        batch_op.alter_column('checkout_id',
                   existing_type=sa.VARCHAR(length=36),
                   nullable=True)
        batch_op.drop_index(op.f('ix_payments_checkout_id'))
        batch_op.create_index(op.f('ix_payments_checkout_id'), ['checkout_id'], unique=False)

    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('balance', sa.Numeric(precision=18, scale=8), server_default='0.0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('balance')

    with op.batch_alter_table('payments') as batch_op:
        batch_op.drop_index(op.f('ix_payments_checkout_id'))
        batch_op.create_index(op.f('ix_payments_checkout_id'), ['checkout_id'], unique=True)
        batch_op.alter_column('checkout_id',
                   existing_type=sa.VARCHAR(length=36),
                   nullable=False)
