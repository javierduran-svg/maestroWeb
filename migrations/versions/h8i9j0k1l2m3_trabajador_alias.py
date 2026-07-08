"""trabajador alias

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-07-07 16:50:00.000000

"""
from alembic import op


revision = 'h8i9j0k1l2m3'
down_revision = 'g7h8i9j0k1l2'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE trabajadores ADD COLUMN IF NOT EXISTS alias VARCHAR(100)')


def downgrade():
    op.execute('ALTER TABLE trabajadores DROP COLUMN IF EXISTS alias')
