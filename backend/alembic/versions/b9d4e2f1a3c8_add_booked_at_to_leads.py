"""add booked_at to leads

Revision ID: b9d4e2f1a3c8
Revises: c3e8a1f09d74
Create Date: 2026-05-10

Adds booked_at column to support manual conversion tracking (Phase 4A).
Nullable — existing leads get NULL. Status 'booked' is set by admin action.
"""
from alembic import op
import sqlalchemy as sa

revision = 'b9d4e2f1a3c8'
down_revision = 'c3e8a1f09d74'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('booked_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('leads', 'booked_at')
