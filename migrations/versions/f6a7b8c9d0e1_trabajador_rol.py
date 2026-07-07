"""trabajador rol

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-01 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('trabajadores', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('rol', sa.String(length=20), nullable=False, server_default='trabajador'),
        )


def downgrade():
    with op.batch_alter_table('trabajadores', schema=None) as batch_op:
        batch_op.drop_column('rol')
