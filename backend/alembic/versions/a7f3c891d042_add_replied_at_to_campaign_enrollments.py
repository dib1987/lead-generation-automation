"""add_replied_at_to_campaign_enrollments

Revision ID: a7f3c891d042
Revises: 048cb7bbebc8
Create Date: 2026-05-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7f3c891d042'
down_revision: Union[str, None] = '048cb7bbebc8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('campaign_enrollments', sa.Column('replied_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('campaign_enrollments', 'replied_at')
