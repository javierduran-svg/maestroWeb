"""Fecha de pago y movimiento contable en reembolsos

Revision ID: q7r8s9t0u1v2
Revises: p6q7r8s9t0u1
Create Date: 2026-08-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'q7r8s9t0u1v2'
down_revision = 'p6q7r8s9t0u1'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'reembolsos' not in tables:
        return
    cols = {c['name'] for c in sa.inspect(bind).get_columns('reembolsos')}
    with op.batch_alter_table('reembolsos', schema=None) as batch_op:
        if 'fecha_reembolso' not in cols:
            batch_op.add_column(sa.Column('fecha_reembolso', sa.Date(), nullable=True))
        if 'movimiento_id' not in cols:
            batch_op.add_column(sa.Column('movimiento_id', sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                'fk_reembolsos_movimiento_id_movimientos',
                'movimientos',
                ['movimiento_id'],
                ['id'],
            )


def downgrade():
    with op.batch_alter_table('reembolsos', schema=None) as batch_op:
        batch_op.drop_constraint(
            'fk_reembolsos_movimiento_id_movimientos', type_='foreignkey',
        )
        batch_op.drop_column('movimiento_id')
        batch_op.drop_column('fecha_reembolso')
