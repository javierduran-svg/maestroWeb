"""movimientos.intro_ep para texto introductorio del Estado de Pago

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-07-15 20:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'm3n4o5p6q7r8'
down_revision = 'l2m3n4o5p6q7'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    cols = {c['name'] for c in sa.inspect(bind).get_columns('movimientos')}
    if 'intro_ep' not in cols:
        with op.batch_alter_table('movimientos', schema=None) as batch_op:
            batch_op.add_column(sa.Column('intro_ep', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('movimientos', schema=None) as batch_op:
        batch_op.drop_column('intro_ep')
