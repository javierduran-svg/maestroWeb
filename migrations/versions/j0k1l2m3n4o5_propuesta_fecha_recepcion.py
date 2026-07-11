"""propuesta fecha_recepcion

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-07-11 10:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'j0k1l2m3n4o5'
down_revision = 'i9j0k1l2m3n4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('propuestas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('fecha_recepcion', sa.Date(), nullable=True))


def downgrade():
    with op.batch_alter_table('propuestas', schema=None) as batch_op:
        batch_op.drop_column('fecha_recepcion')
