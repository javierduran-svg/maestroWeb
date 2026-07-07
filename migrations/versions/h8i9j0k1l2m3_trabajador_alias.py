"""trabajador alias

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-07-07 16:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'h8i9j0k1l2m3'
down_revision = 'g7h8i9j0k1l2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('trabajadores', schema=None) as batch_op:
        batch_op.add_column(sa.Column('alias', sa.String(length=100), nullable=True))


def downgrade():
    with op.batch_alter_table('trabajadores', schema=None) as batch_op:
        batch_op.drop_column('alias')
