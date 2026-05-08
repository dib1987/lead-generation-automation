"""add_unsubscribe_to_leads

Revision ID: f1c9a2d0e8b3
Revises: a7f3c891d042
Create Date: 2026-05-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1c9a2d0e8b3'
down_revision: Union[str, None] = 'a7f3c891d042'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('unsubscribe_token', sa.UUID(), nullable=True))
    op.add_column('leads', sa.Column('unsubscribed_at', sa.DateTime(), nullable=True))
    op.create_index(op.f('ix_leads_unsubscribe_token'), 'leads', ['unsubscribe_token'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_leads_unsubscribe_token'), table_name='leads')
    op.drop_column('leads', 'unsubscribed_at')
    op.drop_column('leads', 'unsubscribe_token')
