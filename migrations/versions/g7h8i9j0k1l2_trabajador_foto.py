"""trabajador foto_path

Revision ID: g7h8i9j0k1l2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-01 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'g7h8i9j0k1l2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('trabajadores', schema=None) as batch_op:
        batch_op.add_column(sa.Column('foto_path', sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table('trabajadores', schema=None) as batch_op:
        batch_op.drop_column('foto_path')
