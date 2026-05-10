"""add utm tracking to leads

Revision ID: c3e8a1f09d74
Revises: f1c9a2d0e8b3
Create Date: 2026-05-09

Adds utm_source, utm_medium, utm_campaign columns to the leads table.
All nullable — existing leads get NULL, which the admin UI renders as '—'.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c3e8a1f09d74'
down_revision = 'f1c9a2d0e8b3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('utm_source',   sa.String(255), nullable=True))
    op.add_column('leads', sa.Column('utm_medium',   sa.String(255), nullable=True))
    op.add_column('leads', sa.Column('utm_campaign', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('leads', 'utm_campaign')
    op.drop_column('leads', 'utm_medium')
    op.drop_column('leads', 'utm_source')
