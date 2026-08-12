"""Monto de ingreso y gasto de comisión en cesión (factoring)

Revision ID: r8s9t0u1v2w3
Revises: q7r8s9t0u1v2
Create Date: 2026-08-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'r8s9t0u1v2w3'
down_revision = 'q7r8s9t0u1v2'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'movimientos' not in tables:
        return
    cols = {c['name'] for c in sa.inspect(bind).get_columns('movimientos')}
    with op.batch_alter_table('movimientos', schema=None) as batch_op:
        if 'monto_ingreso_cesion' not in cols:
            batch_op.add_column(sa.Column('monto_ingreso_cesion', sa.Float(), nullable=True))
        if 'gasto_cesion_id' not in cols:
            batch_op.add_column(sa.Column('gasto_cesion_id', sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                'fk_movimientos_gasto_cesion_id_movimientos',
                'movimientos',
                ['gasto_cesion_id'],
                ['id'],
            )


def downgrade():
    with op.batch_alter_table('movimientos', schema=None) as batch_op:
        batch_op.drop_constraint(
            'fk_movimientos_gasto_cesion_id_movimientos', type_='foreignkey',
        )
        batch_op.drop_column('gasto_cesion_id')
        batch_op.drop_column('monto_ingreso_cesion')
