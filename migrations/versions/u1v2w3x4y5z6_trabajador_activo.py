"""Estado activo del trabajador.

Revision ID: u1v2w3x4y5z6
Revises: t0u1v2w3x4y5
Create Date: 2026-11-09 11:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'u1v2w3x4y5z6'
down_revision = 't0u1v2w3x4y5'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'trabajadores' not in tables:
        return
    cols = {c['name'] for c in sa.inspect(bind).get_columns('trabajadores')}
    if 'activo' in cols:
        return
    with op.batch_alter_table('trabajadores', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'activo', sa.Boolean(), nullable=False, server_default=sa.true(),
        ))


def downgrade():
    with op.batch_alter_table('trabajadores', schema=None) as batch_op:
        batch_op.drop_column('activo')
